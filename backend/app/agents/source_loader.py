"""Helper to fetch source items from configured Sources via their adapters."""
from __future__ import annotations

from datetime import datetime

from app.adapters.sources.base import ContentSource
from app.core.logging import get_logger
from app.domain.entities.source import Source, SourceItem
from app.plugins.registry import PluginKind, PluginRegistry

log = get_logger(__name__)


async def load_items(
    sources: list[Source],
    registry: PluginRegistry,
    since: datetime | None = None,
    limit_per_source: int = 10,
) -> list[SourceItem]:
    out: list[SourceItem] = []
    for src in sources:
        entry = registry.get(PluginKind.SOURCE, src.plugin_name)
        adapter: ContentSource = entry.cls(config=src.config)
        try:
            await adapter.connect()
            count = 0
            async for item in adapter.fetch(since=since):
                out.append(item)
                count += 1
                if count >= limit_per_source:
                    break
        except Exception as exc:                          # noqa: BLE001
            log.warning("source_fetch_failed", source=src.display_name, error=str(exc))
        finally:
            await adapter.disconnect()
    return out
