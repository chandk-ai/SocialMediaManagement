"""Twitter/X engagement source — replies + mentions."""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from app.plugins.registry import register_plugin

from .base import EngagementItem, EngagementKind, EngagementSource


@register_plugin("engagement", "twitter", api_version="1.0")
class TwitterEngagementSource(EngagementSource):
    display_name = "X / Twitter replies + mentions"
    supported_kinds = (EngagementKind.COMMENT, EngagementKind.MENTION,
                       EngagementKind.DM)

    async def poll(self, since: datetime | None = None) -> AsyncIterator[EngagementItem]:
        for i, body in enumerate([
            "Great thread!",
            "@you Got a question about pricing?",
        ]):
            yield EngagementItem(
                external_id=f"tw-reply-{i}",
                plugin_name="twitter",
                account_external_id=self.config.get("account_id", "self"),
                kind=EngagementKind.MENTION if "@" in body else EngagementKind.COMMENT,
                body=body, author_handle=f"@user-{i}",
                occurred_at=datetime.utcnow(),
            )
