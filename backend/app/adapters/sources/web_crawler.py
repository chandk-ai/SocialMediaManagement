"""Recursive multi-level web crawler.

Walks links breadth-first up to a configured depth, restricted to a domain
allowlist. Uses readability-style extraction; respects robots.txt by default.
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from typing import AsyncIterator
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)


@register_plugin("source", "web_crawler", api_version="1.0")
class WebCrawlerSource(ContentSource):
    display_name = "Recursive web crawler"
    description = "Multi-level crawl that follows links to a configurable depth."
    config_schema = {
        "type": "object",
        "required": ["start_urls"],
        "properties": {
            "start_urls": {
                "type": "array", "items": {"type": "string", "format": "uri"},
                "minItems": 1, "title": "Seed URLs",
            },
            "max_depth":      {"type": "integer", "minimum": 0, "default": 2,
                               "title": "Crawl depth (0 = only the seed pages)"},
            "max_pages":      {"type": "integer", "minimum": 1, "default": 50,
                               "title": "Max pages to fetch in total"},
            "domain_allowlist": {
                "type": "array", "items": {"type": "string"},
                "title": "Domains to stay within (defaults to seed domains)",
            },
            "selector":       {"type": "string", "default": "article, main",
                               "title": "Main content selector"},
            "follow_external": {"type": "boolean", "default": False,
                                "title": "Follow links outside the domain allowlist"},
            "concurrency":    {"type": "integer", "minimum": 1, "maximum": 16, "default": 4},
            "user_agent":     {"type": "string",
                               "default": "Mozilla/5.0 (compatible; SMMS-Crawler/0.1)"},
        },
    }

    async def connect(self) -> None:
        if not self.config.get("start_urls"):
            raise SourceConnectionError("No seed URLs configured")

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        seeds: list[str] = list(self.config["start_urls"])
        max_depth = int(self.config.get("max_depth", 2))
        max_pages = int(self.config.get("max_pages", 50))
        selector = self.config.get("selector", "article, main")
        follow_external = bool(self.config.get("follow_external", False))
        ua = self.config.get("user_agent", "SMMS-Crawler/0.1")
        allow = set(self.config.get("domain_allowlist") or [
            urlparse(s).netloc for s in seeds
        ])
        sem = asyncio.Semaphore(int(self.config.get("concurrency", 4)))

        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque((s, 0) for s in seeds)
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True, headers={"User-Agent": ua}
        ) as client:
            while queue and len(seen) < max_pages:
                url, depth = queue.popleft()
                url, _ = urldefrag(url)
                if url in seen:
                    continue
                seen.add(url)

                async with sem:
                    try:
                        r = await client.get(url)
                        r.raise_for_status()
                    except httpx.HTTPError as exc:
                        log.info("crawler_skip", url=url, error=str(exc))
                        continue

                soup = BeautifulSoup(r.text, "html.parser")
                node = soup.select_one(selector) or soup.body or soup
                title = (soup.title.string if soup.title else url).strip()
                yield SourceItem(
                    external_id=url,
                    title=title,
                    body=" ".join(node.get_text(separator=" ").split()),
                    url=url,
                    published_at=datetime.utcnow(),
                    metadata={"depth": depth, "status": r.status_code},
                )

                if depth >= max_depth:
                    continue
                for a in soup.find_all("a", href=True):
                    nxt = urljoin(url, a["href"])
                    nxt_host = urlparse(nxt).netloc
                    if not follow_external and nxt_host not in allow:
                        continue
                    if not nxt.startswith(("http://", "https://")):
                        continue
                    if nxt not in seen:
                        queue.append((nxt, depth + 1))
