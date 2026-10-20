from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiohttp


@dataclass(frozen=True)
class BeatmapLookupResult:
    beatmapset_id: int
    beatmap_id: Optional[int] = None
    provider: str = ""
    status: Optional[str] = None
    has_video: Optional[bool] = None


class IMetadataProvider(ABC):
    name: str

    @abstractmethod
    async def lookup_by_md5(
        self, session: aiohttp.ClientSession, md5: str
    ) -> Optional[BeatmapLookupResult]:
        """
        Returns beatmap metadata for a given beatmap MD5 (osu! stable beatmap hash).
        Returns None if provider cannot find data.
        Should raise only for unexpected failures; normal "not found" should be None.
        """


class IBeatmapsetProvider(ABC):
    name: str

    @abstractmethod
    async def probe_beatmapset(
        self, session: aiohttp.ClientSession, beatmapset_id: int
    ) -> bool:
        """Check that the provider starts returning an archive without downloading it fully"""

    @abstractmethod
    async def download_beatmapset(
        self, session: aiohttp.ClientSession, beatmapset_id: int, dest_path: Path
    ) -> bool:
        """
        Downloads beatmapset .osz by beatmapset_id to dest_path.
        Returns True on success, False if provider cannot serve it.
        Should raise only for unexpected failures.
        """

