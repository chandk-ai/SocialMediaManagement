"""RSS / Atom source adapter."""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

import feedparser
import httpx

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)


@register_plugin("source", "rss", api_version="1.0")
class RSSSource(ContentSource):
    display_name = "RSS / Atom feed"
    description = "Polls a public RSS or Atom feed for new entries."
    config_schema = {
        "type": "object",
        "required": ["feed_url"],
        "properties": {
            "feed_url": {"type": "string", "format": "uri",
                         "title": "Feed URL", "description": "Public RSS or Atom URL"},
            "max_items": {"type": "integer", "minimum": 1, "default": 20,
                          "title": "Max items per fetch"},
        },
    }

    async def connect(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.head(self.config["feed_url"])
                if r.status_code >= 400:
                    raise SourceConnectionError(f"HEAD returned {r.status_code}")
        except httpx.HTTPError as exc:
            raise SourceConnectionError(str(exc)) from exc

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        feed_url = self.config["feed_url"]
        limit = int(self.config.get("max_items", 20))
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(feed_url)
            r.raise_for_status()
            parsed = feedparser.parse(r.text)
        log.info("rss_fetched", entries=len(parsed.entries), feed=feed_url)
        for entry in parsed.entries[:limit]:
            published = _parse_date(entry)
            if since and published and published <= since:
                continue
            yield SourceItem(
                external_id=getattr(entry, "id", entry.link),
                title=getattr(entry, "title", ""),
                body=getattr(entry, "summary", "") or getattr(entry, "description", ""),
                url=getattr(entry, "link", None),
                published_at=published,
                metadata={"author": getattr(entry, "author", None)},
            )


def _parse_date(entry) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    return datetime(*parsed[:6])
