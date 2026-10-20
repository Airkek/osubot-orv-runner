import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from aiohttp import web


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from providers import BeatmapDownloadState, BeatmapLookupResult, download_with_fallback
from providers.mirrors import MirrorDownloadBeatmapsetProvider


def provider(name, probe=True, download=True):
    return SimpleNamespace(
        name=name, probe_beatmapset=AsyncMock(return_value=probe),
        download_beatmapset=AsyncMock(return_value=download),
    )


class DownloadProbingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.destination = Path(self.directory.name) / "beatmap.osz"
        self.session = MagicMock()

    async def test_first_successful_probe_wins_without_waiting_for_slow_mirror(self):
        slow_started = asyncio.Event()
        slow_cancelled = asyncio.Event()

        async def slow_probe(session, beatmapset_id):
            slow_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                slow_cancelled.set()

        async def fast_probe(session, beatmapset_id):
            await slow_started.wait()
            return True

        slow = provider("slow")
        slow.probe_beatmapset.side_effect = slow_probe
        fast = provider("fast")
        fast.probe_beatmapset.side_effect = fast_probe
        state = BeatmapDownloadState(BeatmapLookupResult(123), [slow, fast])
        self.assertTrue(await asyncio.wait_for(
            download_with_fallback(self.session, state, self.destination), timeout=1
        ))
        self.assertTrue(slow_cancelled.is_set())
        slow.download_beatmapset.assert_not_awaited()
        fast.download_beatmapset.assert_awaited_once()
        self.assertEqual(state.attempted, {"fast"})

    async def test_failed_probe_does_not_win_and_is_excluded_from_retry(self):
        failed = provider("failed", probe=False)
        failed_done = asyncio.Event()

        async def reject(session, beatmapset_id):
            failed_done.set()
            return False

        failed.probe_beatmapset.side_effect = reject
        good = provider("good")

        async def accept(session, beatmapset_id):
            await failed_done.wait()
            return True

        good.probe_beatmapset.side_effect = accept
        state = BeatmapDownloadState(BeatmapLookupResult(123), [failed, good])
        self.assertTrue(await download_with_fallback(self.session, state, self.destination))
        self.assertFalse(await download_with_fallback(self.session, state, self.destination))
        self.assertEqual(state.attempted, {"failed", "good"})
        failed.probe_beatmapset.assert_awaited_once()
        failed.download_beatmapset.assert_not_awaited()
        good.download_beatmapset.assert_awaited_once()

    async def test_renderer_retry_can_choose_an_earlier_untried_mirror(self):
        slow_started = asyncio.Event()
        attempts = 0

        async def initially_slow(session, beatmapset_id):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                slow_started.set()
                await asyncio.Event().wait()
            return True

        async def fast_probe(session, beatmapset_id):
            await slow_started.wait()
            return True

        earlier = provider("earlier")
        earlier.probe_beatmapset.side_effect = initially_slow
        later = provider("later")
        later.probe_beatmapset.side_effect = fast_probe
        state = BeatmapDownloadState(BeatmapLookupResult(123), [earlier, later])
        self.assertTrue(await download_with_fallback(self.session, state, self.destination))
        self.assertEqual(state.current_provider, "later")
        self.assertTrue(await download_with_fallback(self.session, state, self.destination))
        self.assertEqual(state.current_provider, "earlier")
        later.probe_beatmapset.assert_awaited_once()
        later.download_beatmapset.assert_awaited_once()
        earlier.download_beatmapset.assert_awaited_once()

    async def test_download_failure_and_partial_file_are_not_retried(self):
        failed = provider("osu.direct")

        async def fail_download(session, beatmapset_id, destination):
            destination.write_bytes(b"partial archive")
            raise OSError("connection lost")

        failed.download_beatmapset.side_effect = fail_download
        good = provider("Nekoha")
        state = BeatmapDownloadState(BeatmapLookupResult(123, status="ranked"), [failed, good])
        self.assertTrue(await download_with_fallback(self.session, state, self.destination))
        self.assertFalse(self.destination.exists())
        self.assertFalse(await download_with_fallback(self.session, state, self.destination))
        failed.download_beatmapset.assert_awaited_once()
        failed.probe_beatmapset.assert_not_awaited()

    async def test_priority_layers_are_not_raced_against_later_layers(self):
        for status, video, expected in (
            ("ranked", None, "osu.direct"),
            ("graveyard", None, "Nekoha"),
            ("graveyard", True, "Mirria"),
        ):
            with self.subTest(status=status, video=video):
                providers = [provider(name) for name in ("Sayobot", "Nekoha", "osu.direct", "Mirria", "Hinamizawa")]
                state = BeatmapDownloadState(BeatmapLookupResult(123, status=status, has_video=video), providers)
                self.assertTrue(await download_with_fallback(self.session, state, self.destination))
                self.assertEqual(state.current_provider, expected)
                for candidate in providers:
                    if candidate.name != expected:
                        candidate.probe_beatmapset.assert_not_awaited()
                        candidate.download_beatmapset.assert_not_awaited()

    async def test_hinamizawa_is_only_used_after_other_sources_fail(self):
        first = provider("first", probe=False)
        last = provider("Hinamizawa")
        state = BeatmapDownloadState(BeatmapLookupResult(123), [last, first])
        self.assertTrue(await download_with_fallback(self.session, state, self.destination))
        self.assertEqual(state.current_provider, "Hinamizawa")
        self.assertEqual(state.attempted, {"first", "Hinamizawa"})
        last.probe_beatmapset.assert_not_awaited()

    async def test_probe_timeout_is_excluded(self):
        failed = provider("timeout")
        failed.probe_beatmapset.side_effect = asyncio.TimeoutError()
        state = BeatmapDownloadState(BeatmapLookupResult(123), [failed])
        self.assertFalse(await download_with_fallback(self.session, state, self.destination))
        self.assertFalse(await download_with_fallback(self.session, state, self.destination))
        failed.probe_beatmapset.assert_awaited_once()

    async def test_cancelling_download_drains_all_probe_tasks(self):
        started = [asyncio.Event(), asyncio.Event()]
        cancelled = [asyncio.Event(), asyncio.Event()]
        candidates = []
        for index in range(2):
            async def probe(session, beatmapset_id, index=index):
                started[index].set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled[index].set()

            candidate = provider(str(index))
            candidate.probe_beatmapset.side_effect = probe
            candidates.append(candidate)
        state = BeatmapDownloadState(BeatmapLookupResult(123), candidates)
        task = asyncio.create_task(download_with_fallback(self.session, state, self.destination))
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(event.is_set() for event in cancelled))
        self.assertEqual(state.attempted, set())

    async def test_attempts_are_local_to_one_replay(self):
        source = provider("source")
        for _ in range(2):
            state = BeatmapDownloadState(BeatmapLookupResult(123), [source])
            self.assertTrue(await download_with_fallback(self.session, state, self.destination))
        self.assertEqual(source.download_beatmapset.await_count, 2)


class MirrorProbeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.status = 200
        self.body = b"PK\x03\x04archive"

        async def serve(request):
            self.requests.append((request.path, request.headers.get("Range")))
            return web.Response(status=self.status, body=self.body)

        app = web.Application()
        app.router.add_get("/b/{id}", serve)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.provider = MirrorDownloadBeatmapsetProvider(f"http://127.0.0.1:{port}", "local", "b")
        self.session = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()

    async def test_probe_supports_range_and_servers_ignoring_range(self):
        for status in (200, 206):
            with self.subTest(status=status):
                self.status = status
                self.assertTrue(await self.provider.probe_beatmapset(self.session, 123))
        self.assertEqual(self.requests, [("/b/123", "bytes=0-3")] * 2)

    async def test_probe_rejects_html_short_body_and_unavailable_sources(self):
        for status, body in ((200, b"<html>"), (200, b"PK"), (404, b""), (429, b""), (503, b"")):
            with self.subTest(status=status, body=body):
                self.status, self.body = status, body
                self.assertFalse(await self.provider.probe_beatmapset(self.session, 123))

    async def test_download_rejects_html_without_writing_an_archive(self):
        self.body = b"<html>error</html>"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "map.osz"
            self.assertFalse(await self.provider.download_beatmapset(self.session, 123, destination))
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
