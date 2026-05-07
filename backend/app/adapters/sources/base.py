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
