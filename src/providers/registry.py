from __future__ import annotations

import os
from typing import List, Tuple

from .mirrors import (
    MirrorDownloadBeatmapsetProvider,
    MirrorV2Md5MetadataProvider,
    NerinyanV2LookupMetadataProvider,
    OsuOfficialV1MetadataProvider,
)
from .types import IBeatmapsetProvider, IMetadataProvider


def build_beatmap_providers() -> Tuple[List[IMetadataProvider], List[IBeatmapsetProvider]]:
    """
    Returns ordered providers. Earlier providers are tried first.
    """
    metadata: List[IMetadataProvider] = []
    beatmapsets: List[IBeatmapsetProvider] = []

    osu_v1_key = (os.getenv("OSU_API_V1_KEY") or "").strip()
    if osu_v1_key:
        # Only register if key specified.
        metadata.append(OsuOfficialV1MetadataProvider(api_key=osu_v1_key))

    # Nerinyan metadata (osu! API v2-like checksum lookup).
    metadata.append(NerinyanV2LookupMetadataProvider())

    # Mirrors compatible with cheesegull-like metadata API.
    metadata.append(
        MirrorV2Md5MetadataProvider(base_url="https://osu.direct", name="osu.direct")
    )
    metadata.append(
        MirrorV2Md5MetadataProvider(base_url="https://catboy.best", name="Mino")
    )

    # Download providers (all supported mirrors).
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(base_url="https://osu.direct", name="osu.direct")
    )
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(
            base_url="https://api.nerinyan.moe", name="Nerinyan"
        )
    )
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(base_url="https://osugatari.ru", name="osu!gatari")
    )
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(base_url="https://catboy.best", name="Mino")
    )

    return metadata, beatmapsets
