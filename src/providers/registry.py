from __future__ import annotations

import os
from typing import List, Tuple

from .mirrors import (
    MirrorDownloadBeatmapsetProvider,
    MirrorV2Md5MetadataProvider,
    NerinyanV2LookupMetadataProvider,
    OsuOfficialV2MetadataProvider,
)
from .types import BeatmapLookupResult, IBeatmapsetProvider, IMetadataProvider


def _preferred_provider_names(metadata: BeatmapLookupResult) -> List[str]:
    preferred = []
    if metadata.status in ("ranked", "approved", "loved"):
        preferred = ["osu.direct", "Nekoha"]
    elif metadata.status in ("graveyard", "pending", "wip"):
        preferred = ["Nekoha", "osu.direct"]
    if metadata.has_video is True:
        preferred.insert(0, "Mirria")
    return preferred


def order_beatmapset_providers(
    providers: List[IBeatmapsetProvider], metadata: BeatmapLookupResult
) -> List[IBeatmapsetProvider]:
    preferred = _preferred_provider_names(metadata)

    priorities = {name: index for index, name in enumerate(preferred)}
    priorities["Hinamizawa"] = len(preferred) + 1
    return sorted(providers, key=lambda provider: priorities.get(provider.name, len(preferred)))


def beatmapset_provider_groups(
    providers: List[IBeatmapsetProvider], metadata: BeatmapLookupResult
) -> List[List[IBeatmapsetProvider]]:
    preferred = _preferred_provider_names(metadata)
    ordered = order_beatmapset_providers(providers, metadata)
    groups = [[provider] for provider in ordered if provider.name in preferred]
    remaining = [p for p in ordered if p.name not in preferred and p.name != "Hinamizawa"]
    if remaining:
        groups.append(remaining)
    groups.extend([provider] for provider in ordered if provider.name == "Hinamizawa")
    return groups


def build_beatmap_providers() -> Tuple[List[IMetadataProvider], List[IBeatmapsetProvider]]:
    """
    Returns ordered providers. Earlier providers are tried first.
    """
    metadata: List[IMetadataProvider] = []
    beatmapsets: List[IBeatmapsetProvider] = []

    client_id = (os.getenv("OSU_API_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("OSU_API_CLIENT_SECRET") or "").strip()
    if client_id and client_secret:
        metadata.append(OsuOfficialV2MetadataProvider(client_id, client_secret))

    # Nerinyan metadata (osu! API v2-like checksum lookup).
    metadata.append(NerinyanV2LookupMetadataProvider())

    # Mirrors compatible with cheesegull-like metadata API.
    metadata.append(
        MirrorV2Md5MetadataProvider(base_url="https://osu.direct", name="osu.direct")
    )
    metadata.append(
        MirrorV2Md5MetadataProvider(base_url="https://catboy.best", name="Mino")
    )
    metadata.append(
        MirrorV2Md5MetadataProvider(
            base_url="https://mirror.hinamizawa.ai", name="Hinamizawa"
        )
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
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(
            base_url="https://mirror.nekoha.moe", name="Nekoha",
            download_path="api/download",
        )
    )
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(
            base_url="https://beatconnect.io", name="BeatConnect", download_path="b",
        )
    )
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(
            base_url="https://txy1.sayobot.cn", name="Sayobot",
            download_path="beatmaps/download/full",
        )
    )
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(
            base_url="https://osudl.org", name="osu!dl", download_path="s",
        )
    )
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(base_url="https://mirror.lisek.cc", name="Mirria")
    )
    beatmapsets.append(
        MirrorDownloadBeatmapsetProvider(
            base_url="https://mirror.hinamizawa.ai/api/v1/hinai", name="Hinamizawa"
        )
    )

    return metadata, beatmapsets
