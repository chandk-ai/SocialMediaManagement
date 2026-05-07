"""EngagementService — drives the post-publish loop.

For each connected platform with a registered EngagementSource adapter,
poll for new comments / DMs / mentions, run them through the TriageAgent,
and emit suggested replies through the same Review channel pipeline that
content posts use.

Approval flow is identical to content review — the human can Approve /
Revise / Reject from WhatsApp, Telegram, or the dashboard.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from app.adapters.engagement.base import EngagementItem, EngagementSource
from app.adapters.llm.base import LLMProvider
from app.agents.triage import TriageAgent, TriageResult
from app.core.logging import get_logger
from app.domain.value_objects.ids import OrgId
from app.plugins.registry import PluginKind, PluginRegistry
from app.repositories.ports import PlatformRepository

log = get_logger(__name__)


class EngagementService:
    def __init__(
        self, platform_repo: PlatformRepository,
        registry: PluginRegistry, llm: LLMProvider,
    ) -> None:
        self.platform_repo = platform_repo
        self.registry = registry
        self.triage = TriageAgent(llm)

    async def poll_org(
        self, org_id: OrgId, since: datetime | None = None,
    ) -> AsyncIterator[TriageResult]:
        """Yield triage results across every connected account that has a
        matching EngagementSource adapter."""
        for platform in await self.platform_repo.list(org_id):
            try:
                entry = self.registry.get(PluginKind.ENGAGEMENT, platform.plugin_name)
            except Exception:
                continue                # no engagement adapter for this plugin
            adapter: EngagementSource = entry.cls(
                credentials=platform.credentials,
                config={**(platform.config or {}),
                        "account_id": platform.account_external_id or ""},
            )
            async for item in adapter.poll(since):
                result = await self.triage.classify(item)
                if result.sentiment.value == "spam":
                    continue
                log.info("triage_classified",
                         plugin=item.plugin_name,
                         sentiment=result.sentiment.value,
                         priority=result.priority,
                         reply=bool(result.draft_reply))
                yield result
