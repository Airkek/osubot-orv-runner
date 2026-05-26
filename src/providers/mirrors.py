from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiofiles
import aiohttp

from .types import BeatmapLookupResult, IBeatmapsetProvider, IMetadataProvider


@dataclass(frozen=True)
class _HttpMirror:
    base_url: str
    name: str


class MirrorV2Md5MetadataProvider(IMetadataProvider):
    """
    Mirrors compatible with cheesegull v2-style API:
      GET {base}/api/v2/md5/{md5} -> JSON with beatmapset_id (and often beatmap_id)
    """

    def __init__(self, base_url: str, name: str):
        self._mirror = _HttpMirror(base_url=base_url.rstrip("/"), name=name)
        self.name = name

    async def lookup_by_md5(
        self, session: aiohttp.ClientSession, md5: str
    ) -> Optional[BeatmapLookupResult]:
        url = f"{self._mirror.base_url}/api/v2/md5/{md5}"
        async with session.get(url) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                return None
            data = await resp.json()

        beatmapset_id = data.get("beatmapset_id")
        if not beatmapset_id:
            return None

        beatmap_id = data.get("beatmap_id")
        try:
            beatmapset_id_i = int(beatmapset_id)
        except Exception:
            return None

        beatmap_id_i: Optional[int] = None
        if beatmap_id is not None:
            try:
                beatmap_id_i = int(beatmap_id)
            except Exception:
                beatmap_id_i = None

        return BeatmapLookupResult(
            beatmapset_id=beatmapset_id_i, beatmap_id=beatmap_id_i, provider=self.name
        )


class MirrorDownloadBeatmapsetProvider(IBeatmapsetProvider):
    """
    Simple cheesegul mirror:
      GET {base}/d/{beatmapset_id} -> .osz bytes
    """

    def __init__(self, base_url: str, name: str):
        self._mirror = _HttpMirror(base_url=base_url.rstrip("/"), name=name)
        self.name = name

    async def download_beatmapset(
        self, session: aiohttp.ClientSession, beatmapset_id: int, dest_path: Path
    ) -> bool:
        url = f"{self._mirror.base_url}/d/{beatmapset_id}"
        async with session.get(url) as resp:
            if resp.status == 404:
                return False
            if resp.status != 200:
                return False
            content = await resp.read()

        async with aiofiles.open(dest_path, "wb") as f:
            await f.write(content)
        return True


class OsuOfficialV1MetadataProvider(IMetadataProvider):
    """
    Official osu! API v1 beatmaps lookup:
      GET https://osu.ppy.sh/api/get_beatmaps?k=APIKEY&h=MD5
    """

    def __init__(self, api_key: str):
        self.name = "osu-official-v1"
        self._api_key = api_key

    async def lookup_by_md5(
        self, session: aiohttp.ClientSession, md5: str
    ) -> Optional[BeatmapLookupResult]:
        url = "https://osu.ppy.sh/api/get_beatmaps"
        params = {"k": self._api_key, "h": md5}
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

        if not isinstance(data, list) or not data:
            return None

        first = data[0]
        beatmapset_id = first.get("beatmapset_id")
        beatmap_id = first.get("beatmap_id")
        if not beatmapset_id:
            return None

        try:
            beatmapset_id_i = int(beatmapset_id)
        except Exception:
            return None

        beatmap_id_i: Optional[int] = None
        if beatmap_id is not None:
            try:
                beatmap_id_i = int(beatmap_id)
            except Exception:
                beatmap_id_i = None

        return BeatmapLookupResult(
            beatmapset_id=beatmapset_id_i, beatmap_id=beatmap_id_i, provider=self.name
        )


class NerinyanV2LookupMetadataProvider(IMetadataProvider):
    """
    Nerinyan osu! API v2-like lookup:
      GET https://api.nerinyan.moe/v2/beatmaps/lookup?checksum=MD5
    """

    def __init__(self):
        self.name = "nerinyan-v2-lookup"
        self._base_url = "https://api.nerinyan.moe"

    async def lookup_by_md5(
        self, session: aiohttp.ClientSession, md5: str
    ) -> Optional[BeatmapLookupResult]:
        url = f"{self._base_url}/v2/beatmaps/lookup"
        params = {"checksum": md5}
        async with session.get(url, params=params) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                return None
            data = await resp.json()

        # Response is a single beatmap object.
        if not isinstance(data, dict):
            return None

        beatmapset_id = data.get("beatmapset_id")
        beatmap_id = data.get("id")
        if not beatmapset_id:
            return None

        try:
            beatmapset_id_i = int(beatmapset_id)
        except Exception:
            return None

        beatmap_id_i: Optional[int] = None
        if beatmap_id is not None:
            try:
                beatmap_id_i = int(beatmap_id)
            except Exception:
                beatmap_id_i = None

        return BeatmapLookupResult(
            beatmapset_id=beatmapset_id_i, beatmap_id=beatmap_id_i, provider=self.name
        )
