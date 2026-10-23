import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from providers import BeatmapLookupResult, build_beatmap_providers, order_beatmapset_providers
from providers.mirrors import (
    MirrorV2Md5MetadataProvider,
    NerinyanV2LookupMetadataProvider,
    OsuOfficialV2MetadataProvider,
)


def response(status, data=None):
    result = MagicMock()
    result.status = status
    result.json = AsyncMock(return_value=data)
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=False)
    return result


def token_response(token="access-token"):
    return response(200, {"access_token": token, "expires_in": 3600})


class OfficialMetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = OsuOfficialV2MetadataProvider("12345", "client-secret")
        self.session = MagicMock()
        self.session.post.return_value = token_response()
        self.session.get.return_value = response(200, {"beatmapset_id": 123, "id": 456})

    async def test_lookup_uses_public_oauth_and_checksum(self):
        result = await self.provider.lookup_by_md5(self.session, "beatmap-md5")
        self.assertEqual(result, BeatmapLookupResult(123, 456, "osu-official-v2"))
        self.session.post.assert_called_once_with(
            "https://osu.ppy.sh/oauth/token",
            data={
                "client_id": "12345",
                "client_secret": "client-secret",
                "grant_type": "client_credentials",
                "scope": "public",
            },
        )
        self.session.get.assert_called_once_with(
            "https://osu.ppy.sh/api/v2/beatmaps/lookup",
            params={"checksum": "beatmap-md5"},
            headers={"Authorization": "Bearer access-token", "Accept": "application/json"},
        )

    async def test_cached_token_survives_session_changes(self):
        await self.provider.lookup_by_md5(self.session, "first")
        next_session = MagicMock()
        next_session.get.return_value = self.session.get.return_value
        await self.provider.lookup_by_md5(next_session, "second")
        next_session.post.assert_not_called()
        self.assertEqual(
            next_session.get.call_args.kwargs["headers"]["Authorization"],
            "Bearer access-token",
        )

    async def test_expiring_token_is_renewed(self):
        self.session.post.side_effect = [token_response(), token_response("renewed")]
        with patch("providers.mirrors.time.monotonic", return_value=100):
            await self.provider.lookup_by_md5(self.session, "first")
        with patch("providers.mirrors.time.monotonic", return_value=3671):
            await self.provider.lookup_by_md5(self.session, "second")
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(
            self.session.get.call_args.kwargs["headers"]["Authorization"],
            "Bearer renewed",
        )

    async def test_unauthorized_lookup_renews_token_once(self):
        self.session.post.side_effect = [token_response(), token_response("renewed")]
        self.session.get.side_effect = [response(401), self.session.get.return_value]
        result = await self.provider.lookup_by_md5(self.session, "md5")
        self.assertEqual(result.beatmapset_id, 123)
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(
            self.session.get.call_args.kwargs["headers"]["Authorization"],
            "Bearer renewed",
        )

    async def test_repeated_unauthorized_response_stops_retrying(self):
        self.session.get.return_value = response(401)
        self.assertIsNone(await self.provider.lookup_by_md5(self.session, "md5"))
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(self.session.get.call_count, 2)

    async def test_unavailable_beatmap_allows_fallback(self):
        for status in (404, 429, 500):
            with self.subTest(status=status):
                self.session.get.return_value = response(status)
                self.assertIsNone(await self.provider.lookup_by_md5(self.session, "md5"))

    async def test_oauth_failure_skips_lookup(self):
        self.session.post.return_value = response(401)
        self.assertIsNone(await self.provider.lookup_by_md5(self.session, "md5"))
        self.session.get.assert_not_called()

    async def test_invalid_oauth_response_skips_lookup(self):
        for data in ([], {}, {"access_token": "token", "expires_in": "invalid"},
                     {"access_token": "token", "expires_in": 0}):
            with self.subTest(data=data):
                self.session.post.return_value = response(200, data)
                self.assertIsNone(await self.provider.lookup_by_md5(self.session, "md5"))
        self.session.get.assert_not_called()

    async def test_invalid_metadata_allows_fallback(self):
        for data in ([], {}, {"beatmapset_id": "invalid"}):
            with self.subTest(data=data):
                self.session.get.return_value = response(200, data)
                self.assertIsNone(await self.provider.lookup_by_md5(self.session, "md5"))

    async def test_render_uses_mirror_after_official_authentication_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = os.getcwd()
            try:
                os.chdir(directory)
                main = importlib.import_module("main")
            finally:
                os.chdir(previous_directory)

        manager = main.RenderManager()
        mirror = SimpleNamespace(
            name="metadata-mirror",
            lookup_by_md5=AsyncMock(return_value=BeatmapLookupResult(123, 456)),
        )
        manager._metadata_providers = [self.provider, mirror]
        manager._realm_reader.has_beatmap = AsyncMock(return_value=False)
        manager._download_beatmapset_with_fallback = AsyncMock(return_value=Path("beatmap.osz"))
        self.session.post.return_value = response(401)
        with patch.object(main, "read_replay_hashes", return_value=SimpleNamespace(
            replay_hash="replay-md5", beatmap_hash="beatmap-md5"
        )), patch.object(main.aiohttp, "ClientSession") as session_factory:
            session_factory.return_value.__aenter__.return_value = self.session
            result = await manager.process_replay_file("replay.osr")

        self.assertEqual(result[0], Path("beatmap.osz"))
        self.assertEqual(result[1].metadata.beatmapset_id, 123)
        self.assertEqual(result[1].providers, manager._beatmapset_providers)
        mirror.lookup_by_md5.assert_awaited_once_with(self.session, "beatmap-md5")
        manager._download_beatmapset_with_fallback.assert_awaited_once_with(result[1])

    async def test_metadata_providers_preserve_set_status_and_video(self):
        for provider in (
            self.provider,
            NerinyanV2LookupMetadataProvider(),
            MirrorV2Md5MetadataProvider("https://mirror.example", "mirror"),
        ):
            with self.subTest(provider=provider.name):
                self.session.get.return_value = response(200, {
                    "beatmapset_id": 123, "id": 456, "status": "pending",
                    "beatmapset": {"status": "graveyard", "video": True},
                })
                result = await provider.lookup_by_md5(self.session, "md5")
                self.assertEqual(result.status, "graveyard")
                self.assertIs(result.has_video, True)

    async def test_invalid_video_flag_is_unknown(self):
        self.session.get.return_value = response(200, {
            "beatmapset_id": 123, "status": "ranked", "beatmapset": {"video": "false"},
        })
        result = await self.provider.lookup_by_md5(self.session, "md5")
        self.assertEqual(result.status, "ranked")
        self.assertIsNone(result.has_video)


class MetadataRegistrationTests(unittest.TestCase):
    def test_official_provider_reuses_viewer_credentials_and_precedes_mirrors(self):
        with patch.dict(os.environ, {
            "OSU_API_CLIENT_ID": "12345", "OSU_API_CLIENT_SECRET": "client-secret"
        }, clear=True):
            metadata, beatmapsets = build_beatmap_providers()
        self.assertIsInstance(metadata[0], OsuOfficialV2MetadataProvider)
        self.assertEqual([p.name for p in metadata[1:]], ["nerinyan-v2-lookup", "osu.direct", "Mino", "Hinamizawa"])
        self.assertEqual(
            [p.name for p in beatmapsets],
            ["osu.direct", "Nerinyan", "osu!gatari", "Mino", "Nekoha", "BeatConnect", "Sayobot", "osu!dl", "Mirria", "Hinamizawa"],
        )

    def test_missing_credentials_keep_mirrors_without_v1(self):
        for credentials in ({}, {"OSU_API_CLIENT_ID": "12345"},
                            {"OSU_API_CLIENT_SECRET": "secret"},
                            {"OSU_API_CLIENT_ID": " ", "OSU_API_CLIENT_SECRET": "secret"},
                            {"OSU_API_V1_KEY": "obsolete"}):
            with self.subTest(credentials=credentials), patch.dict(os.environ, credentials, clear=True):
                metadata, _ = build_beatmap_providers()
                self.assertEqual([p.name for p in metadata], ["nerinyan-v2-lookup", "osu.direct", "Mino", "Hinamizawa"])


class HinamizawaProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        metadata, beatmapsets = build_beatmap_providers()
        self.metadata = next(p for p in metadata if p.name == "Hinamizawa")
        self.download = next(p for p in beatmapsets if p.name == "Hinamizawa")
        self.assertIs(metadata[-1], self.metadata)
        self.assertIs(beatmapsets[-1], self.download)

    async def test_checksum_lookup_parses_osu_v2_ids(self):
        session = MagicMock()
        session.get.return_value = response(200, {"beatmapset_id": 320118, "id": 736215})
        result = await self.metadata.lookup_by_md5(session, "beatmap-md5")
        self.assertEqual(result, BeatmapLookupResult(320118, 736215, "Hinamizawa"))
        session.get.assert_called_once_with(
            "https://mirror.hinamizawa.ai/api/v2/md5/beatmap-md5"
        )

    async def test_download_uses_binary_endpoint(self):
        session = MagicMock()
        session.get.return_value = response(200)
        content = b"PK\x03\x04archive"
        session.get.return_value.read = AsyncMock(return_value=content)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "beatmap.osz"
            self.assertTrue(await self.download.download_beatmapset(session, 320118, destination))
            self.assertEqual(destination.read_bytes(), content)
        session.get.assert_called_once_with(
            "https://mirror.hinamizawa.ai/api/v1/hinai/d/320118"
        )

    async def test_unavailable_mirror_allows_fallback(self):
        for status in (404, 429, 503):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                session = MagicMock()
                session.get.return_value = response(status)
                destination = Path(directory) / "beatmap.osz"
                self.assertIsNone(await self.metadata.lookup_by_md5(session, "beatmap-md5"))
                self.assertFalse(await self.download.download_beatmapset(session, 320118, destination))
                self.assertFalse(destination.exists())


class NekohaProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        metadata, beatmapsets = build_beatmap_providers()
        self.download = next(p for p in beatmapsets if p.name == "Nekoha")
        self.assertNotIn("Nekoha", [p.name for p in metadata])

    async def test_download_uses_nekoha_api_endpoint(self):
        session = MagicMock()
        session.get.return_value = response(200)
        content = b"PK\x03\x04archive"
        session.get.return_value.read = AsyncMock(return_value=content)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "beatmap.osz"
            self.assertTrue(await self.download.download_beatmapset(session, 328098, destination))
            self.assertEqual(destination.read_bytes(), content)
        session.get.assert_called_once_with(
            "https://mirror.nekoha.moe/api/download/328098"
        )

    async def test_unavailable_archive_allows_fallback(self):
        for status in (404, 410, 429, 500):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                session = MagicMock()
                session.get.return_value = response(status)
                destination = Path(directory) / "beatmap.osz"
                self.assertFalse(await self.download.download_beatmapset(session, 328098, destination))
                self.assertFalse(destination.exists())


class AdaptiveDownloadOrderTests(unittest.TestCase):
    def setUp(self):
        _, self.providers = build_beatmap_providers()
        self.original_names = [p.name for p in self.providers]

    def ordered_names(self, status=None, has_video=None):
        ordered = order_beatmapset_providers(
            self.providers, BeatmapLookupResult(123, status=status, has_video=has_video)
        )
        self.assertEqual([p.name for p in self.providers], self.original_names)
        self.assertCountEqual(ordered, self.providers)
        self.assertEqual(ordered[-1].name, "Hinamizawa")
        return [p.name for p in ordered]

    def test_ranked_approved_and_loved_try_osu_direct_then_nekoha(self):
        for status in ("ranked", "approved", "loved"):
            with self.subTest(status=status):
                self.assertEqual(self.ordered_names(status)[:2], ["osu.direct", "Nekoha"])

    def test_unranked_try_nekoha_then_osu_direct(self):
        for status in ("graveyard", "pending", "wip"):
            with self.subTest(status=status):
                self.assertEqual(self.ordered_names(status)[:2], ["Nekoha", "osu.direct"])

    def test_video_tries_mirria_before_status_specific_order(self):
        for status, expected in (
            ("ranked", ["Mirria", "osu.direct", "Nekoha"]),
            ("graveyard", ["Mirria", "Nekoha", "osu.direct"]),
        ):
            with self.subTest(status=status):
                self.assertEqual(self.ordered_names(status, has_video=True)[:3], expected)

    def test_unknown_status_keeps_default_order(self):
        for status in (None, "qualified", "unknown"):
            with self.subTest(status=status):
                self.assertEqual(self.ordered_names(status), self.original_names)

    def test_non_video_map_does_not_promote_mirria(self):
        for video in (False, None):
            with self.subTest(video=video):
                names = self.ordered_names("ranked", has_video=video)
                self.assertEqual(names[-2], "Mirria")


class AdaptiveRenderFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.previous_directory = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, self.previous_directory)
        self.main = importlib.import_module("main")
        self.manager = self.main.RenderManager()
        Path("temp").mkdir()
        Path("renders").mkdir(exist_ok=True)

    async def test_renderer_retry_keeps_adaptive_order_without_changing_registry(self):
        providers = []
        for name in ("osu.direct", "Nekoha"):
            async def download(session, beatmapset_id, destination, provider_name=name):
                self.assertEqual(beatmapset_id, 123)
                destination.write_text(provider_name)
                return True

            providers.append(SimpleNamespace(
                name=name, download_beatmapset=AsyncMock(side_effect=download),
                probe_beatmapset=AsyncMock(return_value=True),
            ))
        self.manager._beatmapset_providers = providers
        self.manager._metadata_providers = [SimpleNamespace(
            name="metadata", lookup_by_md5=AsyncMock(return_value=BeatmapLookupResult(
                123, status="graveyard", has_video=False
            )),
        )]
        self.manager._realm_reader.has_beatmap = AsyncMock(return_value=False)
        self.manager.delete_after_delay = AsyncMock()
        attempted_archives = []

        async def render(cmd, timeout):
            archive = Path(cmd[cmd.index("-osz") + 1])
            attempted_archives.append(archive.read_text())
            if len(attempted_archives) == 1:
                return b"", b"Beatmap not found", SimpleNamespace(returncode=1), False
            Path(cmd[cmd.index("-O") + 1]).write_bytes(b"video")
            return b"", b"", SimpleNamespace(returncode=0), False

        with patch.object(self.main, "read_replay_hashes", return_value=SimpleNamespace(
            replay_hash="replay-md5", beatmap_hash="beatmap-md5"
        )), patch.object(self.main, "run_with_timeout", side_effect=render), \
             patch.object(self.main.aiohttp, "ClientSession"):
            await self.manager._process_render(7, "replay.osr", "config.json", None, False)

        self.assertEqual(attempted_archives, ["Nekoha", "osu.direct"])
        self.assertEqual([p.name for p in self.manager._beatmapset_providers], ["osu.direct", "Nekoha"])
        self.assertEqual(self.manager.renders[7], "done")
        for provider in providers:
            self.assertEqual(provider.download_beatmapset.await_count, 1)

    async def test_cached_beatmap_skips_metadata_and_downloads(self):
        self.manager._realm_reader.has_beatmap = AsyncMock(return_value=True)
        self.manager._download_beatmapset_with_fallback = AsyncMock()
        metadata = SimpleNamespace(name="metadata", lookup_by_md5=AsyncMock())
        self.manager._metadata_providers = [metadata]
        with patch.object(self.main, "read_replay_hashes", return_value=SimpleNamespace(
            replay_hash="replay-md5", beatmap_hash="beatmap-md5"
        )):
            self.assertEqual(await self.manager.process_replay_file("replay.osr"), (None, None))
        metadata.lookup_by_md5.assert_not_awaited()
        self.manager._download_beatmapset_with_fallback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
