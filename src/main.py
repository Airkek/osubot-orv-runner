import shutil
import uuid
import aiofiles
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.params import Form
from fastapi.responses import FileResponse
from osrparse import Replay
import aiohttp
import asyncio
from pathlib import Path
import traceback
from datetime import datetime
from typing import Dict, Optional, List
import orv_config_manager
from database import db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("orv")

app = FastAPI()

RENDER_OUTPUT_DIR = "renders"
TEMP_DIR = "temp"

BEATMAP_MIRRORS_METADATA = [
    "https://osu.direct",
    "https://catboy.best"
]

BEATMAP_MIRRORS_DOWNLOAD = [
    "https://osu.direct",
    "https://api.nerinyan.moe",

    # mino and gatari have SOO much beatmaps being outdated... even ranked beatmaps can be outdated there, so maybe we should not use it for downloading
    "https://osugatari.ru",
    "https://catboy.best",
]

ORV_EXEC = r"/app/osu-replay-viewer/osu-replay-viewer"

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

    def _get_skin_filename_key(self, skin: str, custom: bool) -> str:
        return f"custom_{skin}" if custom else skin

    async def _store_imported_metadata(self, stdout: bytes, skin_filename_key: Optional[str]):
        if not stdout:
            return

        text = stdout.decode(errors="ignore") if stdout else ""
        skin_id = None
        skin_name = None
        beatmapset_online_id = None
        beatmap_hashes: List[str] = []
        beatmap_md5_hashes: List[str] = []
        beatmap_online_ids: List[str] = []

        for line in text.splitlines():
            if line.startswith("IMPORTED_SKIN_ID::"):
                skin_id = line.split("::", 1)[1].strip()
            elif line.startswith("IMPORTED_SKIN_NAME::"):
                skin_name = line.split("::", 1)[1].strip()
            elif line.startswith("IMPORTED_BEATMAPSET_ONLINE_ID::"):
                beatmapset_online_id = line.split("::", 1)[1].strip()
            elif line.startswith("IMPORTED_BEATMAPSET_BEATMAP_HASHES::"):
                hashes = line.split("::", 1)[1].strip()
                beatmap_hashes = [h for h in hashes.split(",") if h]
            elif line.startswith("IMPORTED_BEATMAPSET_BEATMAP_MD5HASHES::"):
                md5_hashes = line.split("::", 1)[1].strip()
                beatmap_md5_hashes = [h for h in md5_hashes.split(",") if h]
            elif line.startswith("IMPORTED_BEATMAPSET_BEATMAP_ONLINE_IDS::"):
                ids = line.split("::", 1)[1].strip()
                beatmap_online_ids = [i for i in ids.split(",") if i]

        if skin_filename_key and skin_id:
            try:
                await db.save_skin_info(skin_filename_key, skin_name, skin_id)
                logger.info(f"Saved imported skin to DB: {skin_filename_key} -> {skin_id}")
            except Exception as e:
                logger.error(f"Failed to save imported skin: {e}")

        if beatmapset_online_id and beatmap_hashes and beatmap_online_ids:
            if len(beatmap_hashes) != len(beatmap_online_ids):
                logger.warning("Imported beatmap hashes/online IDs count mismatch")
                return
            for i, (beatmap_hash, beatmap_online_id) in enumerate(zip(beatmap_hashes, beatmap_online_ids)):
                md5_hash = beatmap_md5_hashes[i] if i < len(beatmap_md5_hashes) else None
                try:
                    await db.save_imported_beatmap(
                        beatmap_hash,
                        int(beatmap_online_id),
                        int(beatmapset_online_id),
                        md5_hash
                    )
                    logger.info(f"Saved imported beatmap to DB: {beatmap_hash} -> {md5_hash} -> {beatmap_online_id} -> {beatmapset_online_id}")
                except Exception as e:
                    logger.error(f"Failed to save imported beatmap {beatmap_hash}: {e}")
        
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
        beatmap_path = await self.process_replay_file(replay_path)

        # Generate output path
        output_path = Path(RENDER_OUTPUT_DIR) / f"{render_id}.mp4"

        # Build render command
        cmd = [
            ORV_EXEC, "--yes",
            "-ex",
            "-i", "file", str(replay_path),
            "-R",
            "-O", str(output_path),
            "-c", str(config_path)
        ]

        if beatmap_path:
            cmd.extend(["-osz", str(beatmap_path)])

        skin_filename_key = None
        skin_id = None
        if skin:
            logger.info(f"Using skin: {skin} (custom={custom_skin})")
            skin_filename_key = self._get_skin_filename_key(skin, custom_skin)
            skin_id = await db.get_skin_id(skin_filename_key)
            if skin_id:
                cmd.extend(["-s", "id", str(skin_id)])
            else:
                skin_path = await self.download_skin(skin, custom_skin)
                cmd.extend(["-s", "import", str(skin_path)])

        logger.info(f"Starting render for {render_id}")
        stdout, stderr, proc, timed_out = await run_with_timeout(cmd, timeout=900)

        await self._store_imported_metadata(stdout, skin_filename_key if skin and not skin_id else None)
        logger.info(f"Imported metadata storage completed for render {render_id}")

        if timed_out:
            raise RenderException("Renderer killed due to 15min timeout", stdout, stderr)
        elif "Beatmap not found" in stderr.decode(errors="ignore"):
            raise RenderException("Renderer error: Beatmap not found in beatmapset. Probably beatmap is outdated on external beatmaps mirror.", stdout, stderr)
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

    async def process_replay_file(self, replay_path: str) -> Optional[Path]:
        logger.info(f"Parsing replay: {replay_path}")
        try:
            replay = Replay.from_path(replay_path)
            logger.info(f"Replay hash: {replay.replay_hash}")
            logger.info(f"Beatmap hash: {replay.beatmap_hash}")
        except Exception as e:
             logger.error(f"Failed to parse replay: {e}")
             raise e

        imported = await db.get_imported_beatmap(replay.beatmap_hash)
        if imported:
            logger.info(f"Beatmap hash found in import DB, skipping download: {replay.beatmap_hash}")
            return None

        beatmapset_id = None
        beatmap_path = None

        for mirror in BEATMAP_MIRRORS_METADATA:
            async with aiohttp.ClientSession() as session:
                logger.info(f"Trying with {mirror}")
                try:
                    async with session.get(f"{mirror}/api/v2/md5/{replay.beatmap_hash}") as resp:
                        if resp.status != 200:
                            logger.warning(f"Mirror {mirror} returned {resp.status}")
                            continue

                        data = await resp.json()

                        beatmapset_id = data['beatmapset_id']
                        beatmap_path = Path(TEMP_DIR) / f"{uuid.uuid4()}.osz"
                        logger.info(f"Got new beatmapset_id: {beatmapset_id}")
                        break
                except RenderException as e:
                    raise e
                except Exception as e:
                    logger.error(f"Error getting metadata from {mirror}: {e}")
                    continue

        if not beatmapset_id: 
            raise RenderException("Could not get beatmap info from api")

        for mirror in BEATMAP_MIRRORS_DOWNLOAD:
            async with aiohttp.ClientSession() as session:
                try:
                    logger.info(f"Downloading beatmap {beatmapset_id} from {mirror}")
                    async with session.get(f"{mirror}/d/{beatmapset_id}") as dl_resp:
                        if dl_resp.status != 200:
                            raise Exception(f"Response {dl_resp.status}")
                        content = await dl_resp.read()
                        async with aiofiles.open(beatmap_path, "wb") as f:
                            await f.write(content)
                        logger.info(f"Beatmap saved: {beatmap_path} ({len(content)} bytes)")
                        return beatmap_path
                except Exception as e:
                    logger.error(f"Error downloading from {mirror}: {e}")
                    continue

        raise RenderException("Failed to download beatmap")

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
    await db.connect()
    await render_manager.start_processing()
    yield
    logger.info("Application shutting down")
    await render_manager.stop_processing()
    await db.close()
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
        hitsoundVolume: int = Form(50)
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
