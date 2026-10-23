import shutil
import uuid
import aiofiles
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.params import Form
from fastapi.responses import FileResponse
import aiohttp
import asyncio
from pathlib import Path
import traceback
from datetime import datetime
from typing import Dict, Optional
import os
import orv_config_manager
from providers import (
    BeatmapDownloadState,
    build_beatmap_providers,
    download_with_fallback,
    order_beatmapset_providers,
)
from realm_reader import OsuLazerRealmReader
from replay_header import read_replay_hashes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("orv")

app = FastAPI()

RENDER_OUTPUT_DIR = "renders"
TEMP_DIR = "temp"

ORV_EXEC = os.getenv("ORV_EXEC", r"/app/osu-replay-viewer/osu-replay-viewer")
USER_AGENT = (os.getenv("USER_AGENT") or "").strip() or (
    "osubot-orv-runner (+https://github.com/Airkek/osubot-orv-runner)"
)

Path(RENDER_OUTPUT_DIR).mkdir(exist_ok=True)

class RenderException(Exception):
    stdout = None
    stderr = None
    def __init__(self, message, stdout=None, stderr=None):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


for file in Path(RENDER_OUTPUT_DIR).glob("*"):
    try:
        if file.is_file():
            file.unlink()
            logger.info(f"Deleted old render file: {file}")
    except Exception as e:
        logger.error(f"Error cleaning up render file {file}: {e}")

async def run_with_timeout(cmd, timeout):
    # Start the subprocess
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Process timed out after {timeout} seconds. Killing it.")
        proc.kill()
        try:
            stdout, stderr = await proc.communicate()
        except Exception as e:
            await proc.wait()
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            stdout, stderr = b"failed to get stdout", b"failed to get stderr\n\nproc.communicate error:\n\n" + tb.encode()
        return stdout, stderr, proc, True
    else:
        return stdout, stderr, proc, False

class RenderManager:
    def __init__(self):
        self.renders: Dict[int, str] = {}
        self.current_render_id = 0
        self.render_queue = asyncio.Queue()
        self.is_processing = False
        self.process_task = None

        self._metadata_providers, self._beatmapset_providers = build_beatmap_providers()
        self._realm_reader = OsuLazerRealmReader(ORV_EXEC)
        logger.info(
            "Beatmap providers: metadata=%s, beatmapset=%s",
            [p.name for p in self._metadata_providers],
            [p.name for p in self._beatmapset_providers],
        )

    def _get_skin_filename_key(self, skin: str, custom: bool) -> str:
        return f"custom_{skin}" if custom else skin

    async def start_processing(self):
        self.is_processing = True
        self.process_task = asyncio.create_task(self._process_queue())
        logger.info("Queue processor task started")
        
    async def stop_processing(self):
        self.is_processing = False
        if self.process_task:
            await self.process_task
        
    async def _process_queue(self):
        logger.info(f"Queue processor loop started.")
        while self.is_processing:
            try:
                # logger.debug(f"Waiting for queue item, queue size={self.render_queue.qsize()}")
                render_id, replay_path, config_path, skin, custom_skin = await self.render_queue.get()
                logger.info(f"Queue processor started for render_id={render_id}")
                
                self.renders[render_id] = "render"
                
                try:
                    await self._process_render(render_id, replay_path, config_path, skin, custom_skin)
                except Exception as e:
                    await self._handle_render_error(render_id, replay_path, e)
                finally:
                    self.render_queue.task_done()
                    Path(replay_path).unlink()
                    Path(config_path).unlink()
                    
            except Exception as e:
                logger.error(f"Error in queue processing: {e}")
                await asyncio.sleep(1)
    
    async def _process_render(self, render_id: int, replay_path: str, config_path: str, skin: str, custom_skin: bool):
        logger.info(f"Processing render {render_id}")
        # Process replay and download beatmap
        beatmap_path, download_state = await self.process_replay_file(
            replay_path
        )

        # Generate output path
        output_path = Path(RENDER_OUTPUT_DIR) / f"{render_id}.mp4"

        # Build render command
        base_cmd = [
            ORV_EXEC, "--yes",
            "-i", "file", str(replay_path),
            "-R",
            "-O", str(output_path),
            "-c", str(config_path)
        ]

        skin_filename_key = None
        skin_id = None
        if skin:
            logger.info(f"Using skin: {skin} (custom={custom_skin})")
            skin_filename_key = self._get_skin_filename_key(skin, custom_skin)
            skin_id = await self._realm_reader.find_skin_id(skin_filename_key)
            if skin_id:
                base_cmd.extend(["-s", "id", str(skin_id)])
            else:
                skin_path = await self.download_skin(skin, custom_skin)
                base_cmd.extend(["-s", "import", str(skin_path)])

        attempt = 0
        stdout = b""
        stderr = b""
        proc = None
        timed_out = False

        while True:
            attempt += 1
            cmd = list(base_cmd)
            if beatmap_path:
                cmd.extend(["-osz", str(beatmap_path)])

            logger.info(
                "Starting render for %s (attempt=%s beatmapset_provider=%s)",
                render_id,
                attempt,
                download_state.current_provider
                if download_state is not None
                else "none",
            )
            stdout, stderr, proc, timed_out = await run_with_timeout(cmd, timeout=900)

            if timed_out:
                raise RenderException("Renderer killed due to 15min timeout", stdout, stderr)

            stderr_text = stderr.decode(errors="ignore") if stderr else ""
            if "Beatmap not found" in stderr_text and beatmap_path and download_state is not None:
                # Clean up old .osz and retry with next provider(s)
                try:
                    if Path(beatmap_path).exists():
                        Path(beatmap_path).unlink()
                except Exception:
                    pass

                logger.warning(
                    "Beatmap not found in downloaded beatmapset_id=%s; excluding tried providers %s",
                    download_state.metadata.beatmapset_id,
                    sorted(download_state.attempted),
                )
                try:
                    beatmap_path = await self._download_beatmapset_with_fallback(download_state)
                except RenderException as error:
                    raise RenderException(str(error), stdout, stderr) from error
                continue

            break

        if "Beatmap not found" in (stderr.decode(errors="ignore") if stderr else ""):
            raise RenderException("Renderer error: Beatmap not found in beatmapset.", stdout, stderr)
        elif proc.returncode != 0 or not output_path.exists():
            raise RenderException(f"Unknown renderer error", stdout, stderr)

        # Notify success
        self.renders[render_id] = "done"
        logger.info(f"Render {render_id} finished successfully")
        asyncio.create_task(self.delete_after_delay(output_path, render_id))

    async def _handle_render_error(self, render_id: int, replay_path: str, error: Exception):
        frPath = Path("failed_replays")
        frPath.mkdir(exist_ok=True)
        
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d_%H-%M-%S")

        frPath = frPath / f"{now_str}_render_{render_id}"
        frPath.mkdir(exist_ok=True)

        replay_copy_path = frPath / "replay.osr"
        render_error_path = frPath / "log.txt"
        
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        with open(render_error_path, "w") as f:
            f.write(tb)

        if isinstance(error, RenderException):
            if error.stdout or error.stderr:
                with open(render_error_path, "ab") as f:
                    if error.stdout:
                        f.write(b"\n\n=====================STDOUT=====================\n\n")
                        f.write(error.stdout)
                    
                    if error.stderr:
                        f.write(b"\n\n=====================STDERR=====================\n\n")
                        f.write(error.stderr)
        
        shutil.copy(replay_path, replay_copy_path)
        self.renders[render_id] = f"fail\n{str(error)}"

    async def start_render(self, replay_path: str, config_path: str, skin: str, custom_skin: bool):
        render_id = self.current_render_id
        self.current_render_id += 1
        self.renders[render_id] = "render\nin queue"
        logger.info(f"Queuing render_id={render_id}, queue size={self.render_queue.qsize()}")
        
        await self.render_queue.put((render_id, replay_path, config_path, skin, custom_skin))
        return render_id

    async def download_skin(self, skin: str, custom: bool) -> Path:
        if custom:
            url = f"https://link.issou.best/skin/clientdownload/{skin}"
            filename = f"custom_{skin}.osk"
        else:
            url = f"https://dl.issou.best/ordr/skins/{skin}.osk"
            filename = f"{skin}.osk"

        path = Path(TEMP_DIR) / filename # use real skin name to store it in the game

        if path.exists():
            path.unlink()

        logger.info(f"Downloading skin: {filename} from {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to download skin {skin}: HTTP {resp.status}")
                    raise Exception("This skin does not exist.")
                content = await resp.read()
                async with aiofiles.open(path, "wb") as f:
                    await f.write(content)
                logger.info(f"Skin saved: {filename} ({len(content)} bytes)")
        
        return path

    async def _download_beatmapset_with_fallback(
        self, state: BeatmapDownloadState
    ) -> Path:
        beatmap_path = Path(TEMP_DIR) / f"{uuid.uuid4()}.osz"

        timeout = aiohttp.ClientTimeout(total=40, connect=10, sock_read=40)
        headers = {"User-Agent": USER_AGENT}

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            if await download_with_fallback(session, state, beatmap_path):
                return beatmap_path

        raise RenderException("Failed to download beatmapset from all providers")

    async def process_replay_file(
        self, replay_path: str
    ) -> tuple[Optional[Path], Optional[BeatmapDownloadState]]:
        logger.info(f"Parsing replay: {replay_path}")
        try:
            replay = read_replay_hashes(replay_path)
            logger.info(f"Replay hash: {replay.replay_hash}")
            logger.info(f"Beatmap hash: {replay.beatmap_hash}")
        except Exception as e:
             logger.error(f"Failed to parse replay: {e}")
             raise e

        imported = await self._realm_reader.has_beatmap(replay.beatmap_hash)
        if imported:
            logger.info(f"Beatmap hash found in osu!lazer Realm, skipping download: {replay.beatmap_hash}")
            return None, None

        beatmapset_id: Optional[int] = None

        timeout = aiohttp.ClientTimeout(total=20, connect=10, sock_read=20)
        headers = {"User-Agent": USER_AGENT}

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for provider in self._metadata_providers:
                logger.info(f"Trying metadata provider: {provider.name}")
                try:
                    res = await provider.lookup_by_md5(session, replay.beatmap_hash)
                    if not res:
                        continue
                    beatmapset_id = res.beatmapset_id
                    logger.info(
                        "Resolved md5 via %s: beatmapset_id=%s beatmap_id=%s",
                        res.provider or provider.name,
                        res.beatmapset_id,
                        res.beatmap_id,
                    )
                    break
                except Exception as e:
                    logger.error(f"Error getting metadata from {provider.name}: {e}")
                    continue

            if not beatmapset_id:
                raise RenderException("Could not get beatmap info from metadata providers")

        beatmapset_providers = order_beatmapset_providers(self._beatmapset_providers, res)
        logger.info(
            "Download order for beatmapset %s (status=%s video=%s): %s",
            beatmapset_id, res.status, res.has_video,
            [provider.name for provider in beatmapset_providers],
        )
        state = BeatmapDownloadState(res, beatmapset_providers)
        beatmap_path = await self._download_beatmapset_with_fallback(state)
        return beatmap_path, state

    async def delete_after_delay(self, output_path: Path, render_id: int):
        await asyncio.sleep(900)  # 15 minutes
        try:
            if output_path.exists():
                output_path.unlink()
                logger.info(f"Deleted render file: {output_path}")
            if render_id in self.renders:
                del self.renders[render_id]
        except Exception as e:
            logger.error(f"Error deleting render file: {e}")

render_manager = RenderManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting up")
    await render_manager.start_processing()
    yield
    logger.info("Application shutting down")
    await render_manager.stop_processing()
    for file in Path(RENDER_OUTPUT_DIR).glob("*.mp4"):
        file.unlink()
    for file in Path(TEMP_DIR).glob("*.json"):
        file.unlink()
    for file in Path(TEMP_DIR).glob("*.osr"):
        file.unlink()

app = FastAPI(lifespan=lifespan)

@app.get("/available")
async def is_available():
    return "1"

@app.post("/renders")
async def create_render(
        replayFile: UploadFile = File(...),
        resolution: str = Form("1280x720"),
        customSkin: bool = Form(False),
        skin: str = Form(None),
        useSkinHitsounds: bool = Form(True),
        useBeatmapColors: bool = Form(False),
        inGameBGDim: int = Form(75),
        loadVideo: bool = Form(True),
        loadStoryboard: bool = Form(True),
        skip: bool = Form(True),
        globalVolume: int = Form(50),
        musicVolume: int = Form(50),
        hitsoundVolume: int = Form(50),
        scrollSpeed: float = Form(26.0, ge=1.0, le=40.0),
        showScoreboard: bool = Form(False)
):
    # Save replay file
    Path(TEMP_DIR).mkdir(exist_ok=True)
    replayUuid = uuid.uuid4()
    replay_path = Path(TEMP_DIR) / f"{replayUuid}.osr"
    config_path = Path(TEMP_DIR) / f"{replayUuid}.json"
    async with aiofiles.open(replay_path, "wb") as f:
        content = await replayFile.read()
        await f.write(content)
    
    config: orv_config_manager.Config = orv_config_manager.Config()
    config.resolution = resolution
    config.fps = 60
    config.background_dim = inGameBGDim / 100
    config.use_beatmap_colors = useBeatmapColors
    config.use_beatmap_hitsounds = not useSkinHitsounds
    config.show_storyboard_or_video = loadStoryboard or loadVideo
    config.skip_intro = skip
    config.master_volume = globalVolume / 100
    config.music_volume = musicVolume / 100
    config.effects_volume = hitsoundVolume / 100
    config.scroll_speed = scrollSpeed
    config.show_leaderboard = showScoreboard

    async with aiofiles.open(config_path, "w") as f:
        content = orv_config_manager.generate_config(config)
        await f.write(content)

    # Start render process
    render_id = await render_manager.start_render(
        str(replay_path),
        str(config_path),
        skin,
        customSkin
    )

    return {"renderID": render_id}


@app.get("/status/{render_id}")
async def get_status(render_id: int):
    if render_id not in render_manager.renders:
        return "not found"

    return render_manager.renders[render_id]


@app.get("/renders/{render_id}")
async def get_render(render_id: int):
    if render_id not in render_manager.renders or render_manager.renders[render_id] != "done":
        raise HTTPException(status_code=404)
    path = Path(RENDER_OUTPUT_DIR) / f"{render_id}.mp4"
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "no-store"})


class IgnoreStatusPathFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return "/status/" not in msg

access_logger = logging.getLogger("uvicorn.access")
access_logger.addFilter(IgnoreStatusPathFilter())
