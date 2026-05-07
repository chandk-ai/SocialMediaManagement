"""Review service — orchestrates the human-in-the-loop checkpoint.

Responsibilities:
    1. Build a `ReviewMessage` from the run's drafts and dispatch via the
       configured channel adapter (WhatsApp / Instagram / email / Slack / in-app).
    2. Persist a `ReviewSession` so we can correlate the eventual reply.
    3. Apply a decision (APPROVE / REVISE / REJECT) and ask the workflow
       service to continue from the right point.
"""
from __future__ import annotations

from typing import Any

from app.adapters.review_channels.base import (
    DecisionKind,
    ReviewChannel,
    ReviewDecision,
    ReviewMessage,
    parse_decision,
)
from app.core.logging import get_logger
from app.domain.entities.review_session import ReviewSession, ReviewStatus
from app.domain.value_objects.ids import OrgId, ReviewId
from app.plugins.registry import PluginKind, PluginRegistry
from app.repositories.ports import ReviewSessionRepository

log = get_logger(__name__)


class ReviewService:
    def __init__(self, repo: ReviewSessionRepository, registry: PluginRegistry) -> None:
        self.repo = repo
        self.registry = registry

    def channel_adapter(self, name: str, config: dict | None = None) -> ReviewChannel:
        entry = self.registry.get(PluginKind.REVIEW_CHANNEL, name)
        return entry.cls(config=config or {})

    # ── outbound: send drafts for review ─────────────────────────────
    async def request_review(
        self, *, session: ReviewSession, drafts: list[dict],
        channel_config: dict | None = None, headline: str | None = None,
    ) -> ReviewSession:
        channel = self.channel_adapter(session.channel, channel_config)
        message = ReviewMessage(
            headline=headline or "Please review this draft before publishing",
            drafts=drafts,
        )
        ref = await channel.send_for_review(session.recipient, message)
        session.sent_message_ref = ref
        session.drafts_snapshot = drafts
        return await self.repo.add(session)

    # ── inbound: interpret a reply and apply the decision ───────────
    async def apply_reply(
        self, *, channel: str, sender: str, reply_text: str,
        in_reply_to: str | None = None,
    ) -> tuple[ReviewSession | None, ReviewDecision]:
        # Prefer threaded matching when the channel supplies an in_reply_to id
        review = None
        if in_reply_to:
            review = await self.repo.get_by_message_ref(channel, in_reply_to)
        if review is None:
            review = await self.repo.latest_pending_for(channel, sender)
        decision = parse_decision(reply_text)
        if review is None:
            log.info("review_reply_unmatched", channel=channel, sender=sender,
                     decision=decision.kind.value)
            return None, decision
        await self._apply(review, decision)
        return review, decision

    async def apply_decision_via_api(
        self, *, org_id: OrgId, review_id: ReviewId,
        kind: DecisionKind, feedback: str = "",
    ) -> ReviewSession:
        review = await self.repo.get(org_id, review_id)
        if review is None:
            raise ValueError("review not found")
        decision = ReviewDecision(kind=kind, feedback=feedback)
        await self._apply(review, decision)
        return review

    async def _apply(self, review: ReviewSession, decision: ReviewDecision) -> None:
        if decision.kind is DecisionKind.APPROVE:
            review.approve()
        elif decision.kind is DecisionKind.REVISE:
            review.request_revision(decision.feedback or decision.raw_text)
        elif decision.kind is DecisionKind.REJECT:
            review.reject(decision.raw_text)
        else:
            return                                          # UNKNOWN — leave PENDING
        await self.repo.update(review)

    async def acknowledge(self, review: ReviewSession, message: str) -> None:
        try:
            adapter = self.channel_adapter(review.channel)
            await adapter.acknowledge(review.recipient, message)
        except Exception as exc:                            # noqa: BLE001
            log.warning("review_ack_failed", error=str(exc))

    async def list_open(self, org_id: OrgId) -> list[ReviewSession]:
        return await self.repo.list_open(org_id)
