"""Generic web-page scraper using readability-style extraction.

Yields both text (the main article body) and media (Open Graph image,
Twitter card image, plus the first few inline ``<img>`` tags that
exceed a minimum size). All discovered image URLs are re-hosted via
``MediaImportService`` so platforms can fetch them after the source
page is gone / rate-limited.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin
from app.services.media_import import MediaImportError, MediaImportService

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)

# Cap per URL. Hero image + maybe one supporting visual is plenty;
# scraping 30 inline thumbnails would balloon Supabase storage and
# rarely produce useful posts.
_MAX_MEDIA_PER_PAGE = 2


@register_plugin("source", "web_scraper", api_version="1.0")
class WebScraperSource(ContentSource):
    display_name = "Web page"
    description = (
        "Scrapes one or more URLs. Extracts main article text plus "
        "the page's Open Graph / Twitter card image (and the first "
        "couple of inline images as fallback) so generated posts have "
        "real visuals attached."
    )
    config_schema = {
        "type": "object",
        "required": ["urls"],
        "properties": {
            "urls": {"type": "array", "items": {"type": "string", "format": "uri"},
                     "minItems": 1, "title": "URLs"},
            "selector": {"type": "string", "default": "article",
                         "title": "CSS selector for main content"},
        },
    }

    async def connect(self) -> None:
        if not self.config.get("urls"):
            raise SourceConnectionError("No URLs configured")

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        selector = self.config.get("selector", "article")
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for url in self.config["urls"]:
                r = await client.get(url)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                node = soup.select_one(selector) or soup.body or soup
                title = (soup.title.string if soup.title else url).strip()
                body = " ".join(node.get_text(separator=" ").split())

                media: tuple[MediaAsset, ...] = ()
                try:
                    media = await self._extract_page_media(soup, base_url=url)
                except Exception as exc:                              # noqa: BLE001
                    log.warning("web_scraper_media_extract_failed",
                                url=url, error=str(exc))

                yield SourceItem(
                    external_id=url,
                    title=title,
                    body=body,
                    url=url,
                    published_at=datetime.utcnow(),
                    media=media,
                )

    async def _extract_page_media(
        self, soup: BeautifulSoup, base_url: str,
    ) -> tuple[MediaAsset, ...]:
        """Collect image candidates in this priority order:

          1. ``og:image`` — the page author explicitly nominated this
             for social-card use, so it's the right hero choice.
          2. ``twitter:image`` — same idea, alternate convention.
          3. First ``<img>`` tag inside the main content selector that
             looks "substantial" (has width/height ≥ a threshold or
             references a path implying real content rather than a
             tiny social-icon).

        Each URL is normalized to absolute form (since pages serve
        relative ``src`` attributes), then re-hosted in Supabase via
        ``MediaImportService``. Failures are dropped silently — one
        bad image shouldn't sink a successful scrape.
        """
        candidates: list[str] = []

        # 1. og:image
        for tag in soup.find_all("meta", attrs={"property": "og:image"}):
            content = (tag.get("content") or "").strip()
            if content:
                candidates.append(urljoin(base_url, content))

        # 2. twitter:image
        for tag in soup.find_all("meta", attrs={"name": "twitter:image"}):
            content = (tag.get("content") or "").strip()
            if content:
                candidates.append(urljoin(base_url, content))

        # 3. inline <img> — only "substantial" ones, to dodge tracking
        # pixels and tiny social-icon glyphs.
        for img in soup.find_all("img"):
            src = (img.get("src") or "").strip()
            if not src:
                continue
            # Skip data URIs (can't be re-hosted from base64 inline);
            # and tracking pixels.
            if src.startswith("data:"):
                continue
            if _looks_too_small(img):
                continue
            candidates.append(urljoin(base_url, src))

        # De-dupe preserving order, cap.
        seen: set[str] = set()
        deduped: list[str] = []
        for url in candidates:
            if not url.startswith(("http://", "https://")):
                continue
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
            if len(deduped) >= _MAX_MEDIA_PER_PAGE:
                break

        if not deduped:
            return ()

        importer = MediaImportService()
        org_id = str(self.config.get("__org_id__") or "shared")
        assets: list[MediaAsset] = []
        for url in deduped:
            try:
                result = await importer.import_url(
                    org_id=org_id, url=url, filename_hint="web-scraper-image",
                )
                assets.append(MediaAsset(url=result.url, kind=MediaKind.IMAGE))
            except MediaImportError as exc:
                log.info("web_scraper_media_skipped", url=url, error=str(exc))
        return tuple(assets)


def _looks_too_small(img) -> bool:
    """Heuristic: skip imgs that look like icons / pixels.

    Browser-friendly attrs include ``width`` / ``height`` (both can be
    in px or a CSS unit). We treat any image with declared width < 200
    as ornamental. Untagged dimensions are accepted — most real content
    images don't bother with explicit attrs, and we'd rather over-
    include than miss the hero shot.
    """
    for attr in ("width", "height"):
        raw = img.get(attr)
        if not raw:
            continue
        try:
            value = int("".join(c for c in raw if c.isdigit()) or "0")
        except ValueError:
            continue
        if 0 < value < 200:
            return True
    return False
