"""Base contract for content source adapters (RSS, Notion, Drive, ...)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncIterator, ClassVar

from app.domain.entities.source import SourceItem


class SourceConnectionError(Exception):
    """Adapter could not reach the source."""


class ContentSource(ABC):
    plugin_name: ClassVar[str] = ""
    api_version: ClassVar[str] = "1.0"
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    # JSON Schema describing the per-source `config` shape — drives the UI form.
    config_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, config: dict) -> None:
        self.config = config

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        """Yield items newer than `since` (None = all)."""
        if False:                       # pragma: no cover  (shape hint for static type)
            yield  # type: ignore[misc]

    async def disconnect(self) -> None:
        return None

    # ── CMS-mode hooks (Niche #3) ────────────────────────────────────────
    # Default no-ops. Sources that act as the source-of-truth for posts
    # (Notion, Airtable, Google Sheets) override these to write status
    # back to the row after the workflow service publishes it. Sources
    # that are read-only (RSS, web scraping) just inherit the no-ops.

    @property
    def is_cms(self) -> bool:
        """True when this source's rows are publishable units, not just
        reference material the planner reads. Subclasses flip this on
        based on the user's config (e.g. Notion's ``cms_mode`` flag)."""
        return False

    async def mark_published(
        self,
        external_id: str,
        *,
        url: str | None = None,
        platform: str | None = None,
        published_at: datetime | None = None,
    ) -> None:
        """Called after a successful publish so the source can update the
        row's status. Default no-op for read-only sources."""
        return None

    async def mark_failed(
        self,
        external_id: str,
        *,
        error: str,
        platform: str | None = None,
    ) -> None:
        """Called when a publish fails terminally. Default no-op."""
        return None
