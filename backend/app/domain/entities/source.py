from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..value_objects.content import MediaAsset
from ..value_objects.ids import OrgId, SourceId, new_id


@dataclass(slots=True)
class Source:
    """A configured content source (RSS feed, Notion DB, web URL, ...)."""
    id: SourceId
    org_id: OrgId
    plugin_name: str
    display_name: str
    config: dict = field(default_factory=dict)
    is_active: bool = True
    last_fetched_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def create(cls, *, org_id: OrgId, plugin_name: str, display_name: str,
               config: dict | None = None) -> "Source":
        return cls(
            id=SourceId(new_id()),
            org_id=org_id,
            plugin_name=plugin_name,
            display_name=display_name,
            config=config or {},
        )


@dataclass(frozen=True, slots=True)
class SourceItem:
    """A single piece of reference material yielded by a source plugin."""
    external_id: str          # stable ID in the source system (URL, DB row id, ...)
    title: str
    body: str
    url: str | None = None
    published_at: datetime | None = None
    media: tuple[MediaAsset, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
