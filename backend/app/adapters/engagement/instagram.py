"""Instagram engagement source — comments + DMs."""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from app.plugins.registry import register_plugin

from .base import EngagementItem, EngagementKind, EngagementSource


@register_plugin("engagement", "instagram", api_version="1.0")
class InstagramEngagementSource(EngagementSource):
    display_name = "Instagram comments + DMs"
    supported_kinds = (EngagementKind.COMMENT, EngagementKind.DM)

    async def poll(self, since: datetime | None = None) -> AsyncIterator[EngagementItem]:
        for i, body in enumerate([
            "🔥🔥🔥",
            "How can I order this in India?",
        ]):
            yield EngagementItem(
                external_id=f"ig-c-{i}",
                plugin_name="instagram",
                account_external_id=self.config.get("account_id", "self"),
                kind=EngagementKind.COMMENT,
                body=body, author_handle=f"@user_{i}",
                occurred_at=datetime.utcnow(),
            )
