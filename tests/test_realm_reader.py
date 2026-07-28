import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from realm_reader import OsuLazerRealmReader, RealmLookupError


class RealmPathTests(unittest.TestCase):
    def test_selects_most_recent_supported_realm_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Path(directory)
            release = storage / "client.realm"
            debug = storage / "client_51.realm"
            ignored = storage / "client_backup.realm"

            release.touch()
            debug.touch()
            ignored.touch()
            os.utime(release, (1, 1))
            os.utime(debug, (2, 2))
            os.utime(ignored, (3, 3))

            reader = OsuLazerRealmReader("viewer", storage_path=directory)

            self.assertEqual(reader.resolve_realm_path(), debug.resolve())

    def test_rejects_invalid_explicit_realm_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-a-client.realm"
            path.touch()
            reader = OsuLazerRealmReader("viewer", realm_path=str(path))

            with self.assertRaises(RealmLookupError):
                reader.resolve_realm_path()


class RealmLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_beatmap_and_skin_results(self):
        with tempfile.TemporaryDirectory() as directory:
            realm_path = Path(directory) / "client_999.realm"
            realm_path.touch()
            process = Mock()
            process.returncode = 0
            process.communicate = AsyncMock()

            reader = OsuLazerRealmReader("viewer", realm_path=str(realm_path))

            process.communicate.return_value = (b"REALM_BEATMAP_FOUND::true\n", b"")
            with patch(
                "realm_reader.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ):
                self.assertTrue(
                    await reader.has_beatmap("0123456789abcdef0123456789abcdef")
                )

            process.communicate.return_value = (
                b"REALM_SKIN_ID::11111111-2222-3333-4444-555555555555\n",
                b"",
            )
            with patch(
                "realm_reader.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ):
                self.assertEqual(
                    await reader.find_skin_id("custom_skin-key"),
                    "11111111-2222-3333-4444-555555555555",
                )

    async def test_missing_realm_is_a_cache_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = OsuLazerRealmReader(
                "viewer",
                realm_path=str(Path(directory) / "client.realm"),
            )

            with patch(
                "realm_reader.asyncio.create_subprocess_exec",
                new=AsyncMock(),
            ) as create_process:
                self.assertFalse(
                    await reader.has_beatmap("0123456789abcdef0123456789abcdef")
                )
                self.assertIsNone(await reader.find_skin_id("skin-key"))
                create_process.assert_not_awaited()

    async def test_rejects_invalid_lookup_inputs(self):
        reader = OsuLazerRealmReader("viewer")

        with self.assertRaises(RealmLookupError):
            await reader.has_beatmap("not-an-md5")

        with self.assertRaises(RealmLookupError):
            await reader.find_skin_id("../skin")


if __name__ == "__main__":
    unittest.main()
