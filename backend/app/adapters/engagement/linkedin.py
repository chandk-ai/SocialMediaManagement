"""LinkedIn engagement source — comments + mentions on the org's posts."""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from app.plugins.registry import register_plugin

from .base import EngagementItem, EngagementKind, EngagementSource


@register_plugin("engagement", "linkedin", api_version="1.0")
class LinkedInEngagementSource(EngagementSource):
    display_name = "LinkedIn comments + mentions"
    supported_kinds = (EngagementKind.COMMENT, EngagementKind.MENTION)

    async def poll(self, since: datetime | None = None) -> AsyncIterator[EngagementItem]:
        # Reference impl — production calls `/v2/socialActions/{post-urn}/comments`.
        # Yield two stub items so the rest of the pipeline can be exercised.
        for i, body in enumerate([
            "Love this — when's the next post?",
            "We tried this, didn't work. Disappointing.",
        ]):
            yield EngagementItem(
                external_id=f"li-comment-{i}",
                plugin_name="linkedin",
                account_external_id=self.config.get("account_id", "self"),
                kind=EngagementKind.COMMENT,
                body=body, author_handle=f"@stub-user-{i}",
                occurred_at=datetime.utcnow(),
            )
