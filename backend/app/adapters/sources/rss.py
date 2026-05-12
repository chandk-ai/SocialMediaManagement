"""RSS / Atom source adapter."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, AsyncIterator

import feedparser
import httpx

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin
from app.services.media_import import MediaImportError, MediaImportService

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)

# Cap media imports per RSS entry — most feeds put the hero image
# first, and importing every inline <img> would balloon Supabase
# storage usage on news-style feeds.
_MAX_MEDIA_PER_ENTRY = 2

# Loose img-src extractor — robust to attribute order, quote style, and
# trailing slashes. We don't reach for BeautifulSoup here because RSS
# descriptions are typically a few hundred chars and a regex is cheap.
_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


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
            body = (
                getattr(entry, "summary", "")
                or getattr(entry, "description", "")
            )
            media: tuple[MediaAsset, ...] = ()
            try:
                media = await self._extract_entry_media(entry, body)
            except Exception as exc:                                  # noqa: BLE001
                log.warning("rss_media_extract_failed",
                            link=getattr(entry, "link", "?"), error=str(exc))
            yield SourceItem(
                external_id=getattr(entry, "id", entry.link),
                title=getattr(entry, "title", ""),
                body=body,
                url=getattr(entry, "link", None),
                published_at=published,
                media=media,
                metadata={"author": getattr(entry, "author", None)},
            )

    # ── media extraction ──────────────────────────────────────────────────
    async def _extract_entry_media(
        self, entry: Any, body_html: str,
    ) -> tuple[MediaAsset, ...]:
        """Pull image / video references from an RSS entry, in priority
        order:

          1. ``<enclosure type="image/*"|"video/*">`` — the spec's
             intended media-attachment mechanism (still common in
             podcast feeds and image-heavy news feeds).
          2. ``<media:content url="..." medium="image|video">``
             (Media RSS extension, used by Flickr / SmugMug / Yahoo).
          3. ``<media:thumbnail url="...">`` — last-resort thumbnail.
          4. First few ``<img src="...">`` tags in the description HTML.

        Each candidate URL is pushed through ``MediaImportService`` so
        the resulting ``SourceItem.media`` URLs are permanent.
        """
        candidates: list[tuple[str, MediaKind]] = []

        # 1. enclosures
        for enc in getattr(entry, "enclosures", []) or []:
            url = (enc.get("href") or enc.get("url") or "") if isinstance(enc, dict) else getattr(enc, "href", "")
            etype = (enc.get("type") or "").lower() if isinstance(enc, dict) else getattr(enc, "type", "")
            if not url:
                continue
            if etype.startswith("image/"):
                candidates.append((url, MediaKind.IMAGE))
            elif etype.startswith("video/"):
                candidates.append((url, MediaKind.VIDEO))

        # 2 + 3. media:content / media:thumbnail
        for mc in getattr(entry, "media_content", []) or []:
            url = mc.get("url") if isinstance(mc, dict) else ""
            medium = (mc.get("medium") or "").lower() if isinstance(mc, dict) else ""
            mtype = (mc.get("type") or "").lower() if isinstance(mc, dict) else ""
            if not url:
                continue
            if medium == "video" or mtype.startswith("video/"):
                candidates.append((url, MediaKind.VIDEO))
            else:
                candidates.append((url, MediaKind.IMAGE))
        for mt in getattr(entry, "media_thumbnail", []) or []:
            url = mt.get("url") if isinstance(mt, dict) else ""
            if url:
                candidates.append((url, MediaKind.IMAGE))

        # 4. inline <img> in description (after spec-defined fields so
        # we prefer explicit attachments to scraped fallbacks)
        if body_html:
            for m in _IMG_SRC_RE.finditer(body_html):
                candidates.append((m.group(1), MediaKind.IMAGE))

        # De-dupe while preserving order, cap, then import.
        seen: set[str] = set()
        deduped: list[tuple[str, MediaKind]] = []
        for url, kind in candidates:
            if url in seen:
                continue
            seen.add(url)
            deduped.append((url, kind))
            if len(deduped) >= _MAX_MEDIA_PER_ENTRY:
                break

        if not deduped:
            return ()

        importer = MediaImportService()
        org_id = str(self.config.get("__org_id__") or "shared")
        assets: list[MediaAsset] = []
        for url, kind in deduped:
            try:
                result = await importer.import_url(
                    org_id=org_id, url=url,
                    filename_hint=f"rss-{kind.value}",
                )
                assets.append(MediaAsset(url=result.url, kind=kind))
            except MediaImportError as exc:
                log.info("rss_media_skipped", url=url, error=str(exc))
        return tuple(assets)


def _parse_date(entry) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    return datetime(*parsed[:6])
