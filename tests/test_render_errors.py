import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

with tempfile.TemporaryDirectory() as directory:
    previous_directory = os.getcwd()
    try:
        os.chdir(directory)
        main = importlib.import_module("main")
    finally:
        os.chdir(previous_directory)


class RenderErrorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.previous_directory = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, self.previous_directory)
        self.manager = main.RenderManager()
        self.manager.process_replay_file = AsyncMock(return_value=(None, None))
        self.manager._download_beatmapset_with_fallback = AsyncMock()
        Path("renders").mkdir(exist_ok=True)

    async def render_error(self, stderr, returncode=1, timed_out=False):
        stdout = b"Loading player\n"
        with patch.object(main, "run_with_timeout", new=AsyncMock(return_value=(
            stdout, stderr, SimpleNamespace(returncode=returncode), timed_out,
        ))) as run:
            with self.assertRaises(main.RenderException) as raised:
                await self.manager._process_render(7, "replay.osr", "config.json", None, False)
        run.assert_awaited_once()
        self.assertEqual(raised.exception.stdout, stdout)
        self.assertEqual(raised.exception.stderr, stderr)
        self.manager._download_beatmapset_with_fallback.assert_not_awaited()
        return raised.exception

    async def test_pr_26_errors_reach_public_status_and_failed_replay_log(self):
        messages = (
            "Unsupported replay mod(s): STC. Update the viewer to a version that supports these mods",
            "Replay does not specify a ruleset",
            "Unknown replay ruleset ID 255",
            "Unsupported ruleset 'custom' (ID 42): Ruleset could not be loaded",
            "Player could not load the beatmap with the selected ruleset and mods; see runtime.log for details",
        )
        replay = Path("replay.osr")
        replay.write_bytes(b"replay-content")
        for message in messages:
            with self.subTest(message=message):
                stderr = (
                    "error: XDG_RUNTIME_DIR not set in the environment.\n"
                    "ALSA lib pcm_dmix.c:1032: unable to open slave\n"
                    f"[Replay] {message}\n"
                ).encode()
                error = await self.render_error(stderr)
                self.assertEqual(str(error), f"Renderer error: {message}")
                with patch.object(main, "render_manager", self.manager):
                    await self.manager._handle_render_error(7, str(replay), error)
                    client = TestClient(main.app)
                    try:
                        status = client.get("/status/7")
                    finally:
                        client.close()
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json(), f"fail\nRenderer error: {message}")
                log_path = max(Path("failed_replays").glob("*/log.txt"), key=lambda path: path.stat().st_mtime_ns)
                log = log_path.read_bytes()
                self.assertIn(str(error).encode(), log)
                self.assertIn(stderr, log)
                self.assertEqual(log_path.with_name("replay.osr").read_bytes(), b"replay-content")

    async def test_explicit_replay_error_does_not_retry_downloads(self):
        archive = Path("beatmap.osz")
        archive.write_bytes(b"archive")
        state = SimpleNamespace(current_provider="mirror")
        self.manager.process_replay_file.return_value = (archive, state)
        message = "Unsupported replay mod(s): STC"
        error = await self.render_error(f"Beatmap not found\n[Replay] {message}\n".encode())
        self.assertEqual(str(error), f"Renderer error: {message}")
        self.assertTrue(archive.exists())

    async def test_explicit_error_is_used_even_if_output_exists(self):
        Path("renders/7.mp4").write_bytes(b"partial video")
        error = await self.render_error(b"[Replay] Unknown replay ruleset ID 255\n")
        self.assertEqual(str(error), "Renderer error: Unknown replay ruleset ID 255")

    async def test_timeout_keeps_its_own_reason(self):
        error = await self.render_error(b"[Replay] Unknown replay ruleset ID 255\n", timed_out=True)
        self.assertEqual(str(error), "Renderer killed due to 15min timeout")

    async def test_unmarked_stderr_does_not_expose_environment_noise(self):
        error = await self.render_error(b"ALSA lib: unable to open slave\n")
        self.assertEqual(str(error), "Unknown renderer error")

    async def test_empty_replay_error_keeps_generic_fallback(self):
        error = await self.render_error(b"[Replay] \r\n")
        self.assertEqual(str(error), "Unknown renderer error")


if __name__ == "__main__":
    unittest.main()
