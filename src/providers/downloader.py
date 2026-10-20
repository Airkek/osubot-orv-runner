from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiohttp

from .registry import beatmapset_provider_groups
from .types import BeatmapLookupResult, IBeatmapsetProvider


logger = logging.getLogger("orv")


@dataclass
class BeatmapDownloadState:
    metadata: BeatmapLookupResult
    providers: list[IBeatmapsetProvider]
    attempted: set[str] = field(default_factory=set)
    current_provider: Optional[str] = None


async def _probe_first(
    session: aiohttp.ClientSession,
    state: BeatmapDownloadState,
    providers: list[IBeatmapsetProvider],
) -> Optional[IBeatmapsetProvider]:
    async def probe(provider):
        try:
            available = await provider.probe_beatmapset(session, state.metadata.beatmapset_id)
        except Exception as error:
            logger.info("Probe failed for %s: %s", provider.name, error)
            available = False
        if not available:
            state.attempted.add(provider.name)
            logger.info("Probe refused beatmapset %s via %s", state.metadata.beatmapset_id, provider.name)
            return None
        return provider

    tasks = [asyncio.create_task(probe(provider)) for provider in providers]
    try:
        for completed in asyncio.as_completed(tasks):
            provider = await completed
            if provider is not None:
                return provider
        return None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def download_with_fallback(
    session: aiohttp.ClientSession, state: BeatmapDownloadState, destination: Path
) -> bool:
    blind = {"Hinamizawa"}
    if state.metadata.status in ("ranked", "approved", "loved"):
        blind.add("osu.direct")

    for group in beatmapset_provider_groups(state.providers, state.metadata):
        while remaining := [p for p in group if p.name not in state.attempted]:
            if len(remaining) == 1 and remaining[0].name in blind:
                provider = remaining[0]
            else:
                provider = await _probe_first(session, state, remaining)
            if provider is None:
                break

            state.attempted.add(provider.name)
            try:
                logger.info("Downloading beatmapset %s via %s", state.metadata.beatmapset_id, provider.name)
                if await provider.download_beatmapset(session, state.metadata.beatmapset_id, destination):
                    state.current_provider = provider.name
                    logger.info("Beatmap saved: %s (provider=%s)", destination, provider.name)
                    return True
            except Exception as error:
                logger.error("Error downloading from %s: %s", provider.name, error)
            destination.unlink(missing_ok=True)
    return False
