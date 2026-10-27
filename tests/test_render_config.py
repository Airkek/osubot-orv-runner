import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
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


class RenderConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.previous_directory = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, self.previous_directory)
        self.config = None

        async def capture_render(replay_path, config_path, skin, custom_skin):
            self.assertEqual(Path(replay_path).read_bytes(), b"replay-content")
            self.config = json.loads(Path(config_path).read_text())
            return 42

        self.queue = patch.object(
            main.render_manager, "start_render", AsyncMock(side_effect=capture_render)
        )
        self.start_render = self.queue.start()
        self.addCleanup(self.queue.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def post_render(self, **data):
        return self.client.post(
            "/renders",
            files={"replayFile": ("replay.osr", b"replay-content")},
            data=data,
        )

    def test_existing_clients_keep_encoder_and_game_defaults(self):
        response = self.post_render(scrollSpeed="30")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"renderID": 42})
        self.assertFalse(self.config["game_settings"]["show_leaderboard"])
        self.assertEqual(self.config["game_settings"]["scroll_speed"], 30.0)
        ffmpeg = self.config["ffmpeg_options"]
        self.assertEqual(ffmpeg["video_encoder"], "h264_nvenc")
        self.assertEqual(ffmpeg["video_encoder_preset"], "p7")
        self.assertEqual(ffmpeg["video_encoder_bitrate"], "100M")
        self.assertEqual(ffmpeg["video_rate_control"], "ConstantQuantizer")
        self.assertEqual(ffmpeg["video_quality"], 26)
        self.assertEqual(ffmpeg["video_encoder_gop_size"], 450)
        self.assertEqual(ffmpeg["audio_encoder_bitrate"], 160000)

    def test_scoreboard_and_local_vbr_reach_queued_viewer_config(self):
        with patch.multiple(
            main.orv_config_manager,
            FFMPEG_RATE_CONTROL="VariableBitrate",
            FFMPEG_QUALITY=None,
            FFMPEG_BITRATE="20M",
            FFMPEG_MAX_BITRATE="30M",
            FFMPEG_BUFFER_SIZE="40M",
        ):
            response = self.post_render(showScoreboard="true")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.config["game_settings"]["show_leaderboard"])
        ffmpeg = self.config["ffmpeg_options"]
        self.assertEqual(ffmpeg["video_rate_control"], "VariableBitrate")
        self.assertEqual(ffmpeg["video_encoder_bitrate"], "20M")
        self.assertEqual(ffmpeg["video_encoder_max_bitrate"], "30M")
        self.assertEqual(ffmpeg["video_encoder_buffer_size"], "40M")
        self.assertIsNone(ffmpeg["video_quality"])

    def test_constant_quality_reaches_queued_viewer_config(self):
        with patch.multiple(
            main.orv_config_manager,
            FFMPEG_RATE_CONTROL="ConstantQuality",
            FFMPEG_QUALITY=19.5,
        ):
            response = self.post_render()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.config["ffmpeg_options"]["video_quality"], 19.5)
        self.assertIsNone(self.config["ffmpeg_options"]["video_encoder_max_bitrate"])

    def test_render_request_cannot_override_local_encoder_settings(self):
        response = self.post_render(
            videoRateControl="VariableBitrate",
            videoQuality="5",
            videoEncoderBitrate="1M",
            videoEncoderMaxBitrate="2M",
            videoEncoderBufferSize="3M",
            videoEncoderGopSize="60",
            audioEncoderBitrate="64000",
        )
        self.assertEqual(response.status_code, 200)
        ffmpeg = self.config["ffmpeg_options"]
        self.assertEqual(ffmpeg["video_encoder_bitrate"], "100M")
        self.assertEqual(ffmpeg["video_rate_control"], "ConstantQuantizer")
        self.assertEqual(ffmpeg["video_quality"], 26)
        self.assertEqual(ffmpeg["video_encoder_gop_size"], 450)
        self.assertEqual(ffmpeg["audio_encoder_bitrate"], 160000)
        for field in ("video_encoder_max_bitrate", "video_encoder_buffer_size"):
            self.assertIsNone(ffmpeg[field])

    def test_public_schema_uses_ordr_scoreboard_and_no_encoder_controls(self):
        schema = self.client.get("/openapi.json").json()
        body_ref = schema["paths"]["/renders"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
        fields = schema["components"]["schemas"][body_ref.rsplit("/", 1)[-1]]["properties"]
        self.assertIs(fields["showScoreboard"]["default"], False)
        for field in ("showLeaderboard", "videoRateControl", "videoQuality", "videoEncoderBitrate", "videoEncoderMaxBitrate", "videoEncoderBufferSize", "videoEncoderGopSize", "audioEncoderBitrate"):
            self.assertNotIn(field, fields)

    def test_environment_credentials_are_not_written_to_render_config(self):
        with patch.dict(os.environ, OSU_API_CLIENT_ID="12345", OSU_API_CLIENT_SECRET="test-secret"):
            self.assertEqual(self.post_render(showScoreboard="true").status_code, 200)
        self.assertNotIn("osu_api", self.config)
        self.assertNotIn("test-secret", json.dumps(self.config))


class ViewerEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_viewer_subprocess_inherits_api_v2_credentials(self):
        with patch.dict(os.environ, OSU_API_CLIENT_ID="12345", OSU_API_CLIENT_SECRET="test-secret"):
            stdout, stderr, process, timed_out = await main.run_with_timeout(
                [sys.executable, "-c", "import os; print(os.environ['OSU_API_CLIENT_ID']); print(os.environ['OSU_API_CLIENT_SECRET'])"],
                timeout=10,
            )
        self.assertFalse(timed_out)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout.splitlines(), [b"12345", b"test-secret"])


class RenderDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_preserves_output_and_saves_it_in_failure_log(self):
        stdout, stderr, process, timed_out = await main.run_with_timeout(
            [sys.executable, "-c", (
                "import sys, time; "
                "print('renderer stdout', flush=True); "
                "print('renderer stderr', file=sys.stderr, flush=True); "
                "time.sleep(60)"
            )],
            timeout=1,
        )
        self.assertTrue(timed_out)
        self.assertIsNotNone(process.returncode)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, b"renderer stdout\n")
        self.assertEqual(stderr, b"renderer stderr\n")
        log = await self.save_error(main.RenderException("Renderer timed out", stdout, stderr))
        self.assertIn(b"STDOUT=====================\n\nrenderer stdout\n", log)
        self.assertIn(b"STDERR=====================\n\nrenderer stderr\n", log)

    async def test_failure_log_marks_empty_streams(self):
        log = await self.save_error(main.RenderException("Renderer timed out", b"", b""))
        self.assertIn(b"STDOUT=====================\n\n(empty)\n", log)
        self.assertIn(b"STDERR=====================\n\n(empty)\n", log)

    async def save_error(self, error):
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = os.getcwd()
            try:
                os.chdir(directory)
                replay = Path("replay.osr")
                replay.write_bytes(b"replay-content")
                manager = main.RenderManager()
                await manager._handle_render_error(59, str(replay), error)
                failure = next(Path("failed_replays").iterdir())
                self.assertEqual((failure / "replay.osr").read_bytes(), b"replay-content")
                self.assertEqual(manager.renders[59], f"fail\n{error}")
                return (failure / "log.txt").read_bytes()
            finally:
                os.chdir(previous_directory)


if __name__ == "__main__":
    unittest.main()
