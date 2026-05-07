"""Generic web-page scraper using readability-style extraction."""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError


@register_plugin("source", "web_scraper", api_version="1.0")
class WebScraperSource(ContentSource):
    display_name = "Web page"
    description = "Scrapes one or more URLs and extracts their main text."
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
                yield SourceItem(
                    external_id=url,
                    title=title,
                    body=" ".join(node.get_text(separator=" ").split()),
                    url=url,
                    published_at=datetime.utcnow(),
                )
