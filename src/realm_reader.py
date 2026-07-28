import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional


logger = logging.getLogger("orv.realm")

_CLIENT_REALM_NAME = re.compile(r"^client(?:_(\d+))?\.realm$")
_BEATMAP_MD5 = re.compile(r"^[0-9a-fA-F]{32}$")
_BEATMAP_RESULT_PREFIX = "REALM_BEATMAP_FOUND::"
_SKIN_RESULT_PREFIX = "REALM_SKIN_ID::"
_SKIN_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class RealmLookupError(RuntimeError):
    pass


class OsuLazerRealmReader:
    """Runs the viewer's strictly read-only dynamic Realm lookup commands."""

    def __init__(
        self,
        viewer_executable: str,
        storage_path: Optional[str] = None,
        realm_path: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self._viewer_executable = viewer_executable
        self._storage_path = Path(
            storage_path
            or os.getenv("OSU_LAZER_STORAGE_PATH")
            or "/root/.local/share/osu_replay_viewer"
        )
        explicit_path = realm_path if realm_path is not None else os.getenv("OSU_LAZER_REALM_PATH")
        self._explicit_realm_path = Path(explicit_path) if explicit_path else None
        self._timeout = timeout

    def resolve_realm_path(self) -> Optional[Path]:
        if self._explicit_realm_path is not None:
            raw_path = self._explicit_realm_path
            if raw_path.is_symlink():
                raise RealmLookupError("Refusing to read an osu!lazer Realm through a symlink")

            path = raw_path.resolve()
            if not _CLIENT_REALM_NAME.fullmatch(path.name):
                raise RealmLookupError(
                    "OSU_LAZER_REALM_PATH must point to client.realm or client_<schema>.realm"
                )
            return path if path.is_file() else None

        storage_path = self._storage_path.resolve()
        if not storage_path.is_dir():
            return None

        candidates: list[Path] = []
        for path in storage_path.iterdir():
            if not _CLIENT_REALM_NAME.fullmatch(path.name):
                continue
            if path.is_symlink() or not path.is_file():
                continue

            resolved = path.resolve()
            if resolved.parent != storage_path:
                continue
            candidates.append(resolved)

        if not candidates:
            return None

        # RealmAccess touches the active database's mtime whenever the viewer starts.
        # This reliably selects the active file if both release (client.realm) and
        # debug (client_<schema>.realm) databases are present.
        return max(
            candidates,
            key=lambda path: (
                path.stat().st_mtime_ns,
                self._schema_version_from_name(path.name),
            ),
        )

    async def has_beatmap(self, beatmap_md5: str) -> bool:
        if not _BEATMAP_MD5.fullmatch(beatmap_md5):
            raise RealmLookupError("Beatmap MD5 must contain exactly 32 hexadecimal characters")

        value = await self._run_lookup("--realm-has-beatmap", beatmap_md5.lower())
        if value == "true":
            return True
        if value == "false":
            return False
        raise RealmLookupError(f"Unexpected beatmap lookup result: {value!r}")

    async def find_skin_id(self, archive_name: str) -> Optional[str]:
        if (
            not archive_name
            or len(archive_name) > 255
            or Path(archive_name).name != archive_name
            or "/" in archive_name
            or "\\" in archive_name
        ):
            raise RealmLookupError("Skin archive name is invalid")

        value = await self._run_lookup("--realm-find-skin", archive_name)
        if not value:
            return None
        if not _SKIN_ID.fullmatch(value):
            raise RealmLookupError(f"Unexpected skin lookup result: {value!r}")
        return value.lower()

    async def _run_lookup(self, command: str, value: str) -> str:
        realm_path = self.resolve_realm_path()
        if realm_path is None:
            logger.info("osu!lazer Realm does not exist yet; treating lookup as a cache miss")
            return "false" if command == "--realm-has-beatmap" else ""

        proc = await asyncio.create_subprocess_exec(
            self._viewer_executable,
            command,
            str(realm_path),
            value,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError as error:
            proc.kill()
            await proc.communicate()
            raise RealmLookupError(
                f"Read-only osu!lazer Realm lookup timed out after {self._timeout:g}s"
            ) from error

        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            detail = stderr_text[-2000:] if stderr_text else "no error output"
            raise RealmLookupError(
                f"Read-only osu!lazer Realm lookup exited with {proc.returncode}: {detail}"
            )

        prefix = (
            _BEATMAP_RESULT_PREFIX
            if command == "--realm-has-beatmap"
            else _SKIN_RESULT_PREFIX
        )
        results = [
            line[len(prefix) :].strip()
            for line in stdout_text.splitlines()
            if line.startswith(prefix)
        ]
        if len(results) != 1:
            raise RealmLookupError(
                f"Read-only osu!lazer Realm lookup returned {len(results)} result markers"
            )
        return results[0]

    @staticmethod
    def _schema_version_from_name(name: str) -> int:
        match = _CLIENT_REALM_NAME.fullmatch(name)
        if match is None or match.group(1) is None:
            return -1
        return int(match.group(1))
