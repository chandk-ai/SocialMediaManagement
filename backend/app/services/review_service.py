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
            # Surface the quorum so the channel adapter (e.g. Telegram) can
            # render "any 3 ✅ to publish" in the message text.
            metadata={"quorum_required": session.quorum_required},
        )
        ref = await channel.send_for_review(session.recipient, message)
        session.sent_message_ref = ref
        session.drafts_snapshot = drafts
        return await self.repo.add(session)

    # ── inbound: interpret a reply and apply the decision ───────────
    async def apply_reply(
        self, *, channel: str, sender: str, reply_text: str,
        in_reply_to: str | None = None,
        actor_id: str | None = None,
        actor_handle: str | None = None,
    ) -> tuple[ReviewSession | None, ReviewDecision]:
        """Apply a reviewer reply.

        For 1:1 channels (WhatsApp DM, Instagram DM, email) the first
        decision wins — same as before. For *group* channels (Telegram
        group with multiple admins, identified by ``review.quorum_required
        > 1``) every tap is recorded as a vote and the run only resumes
        once the quorum is met. ``actor_id`` is the per-user identity
        within the channel (Telegram from.id, etc.) — required for
        quorum dedupe."""
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
        # Quorum path — only kicks in when the session was created with
        # quorum_required > 1 AND the channel gave us an actor_id.
        if review.quorum_required > 1 and actor_id:
            await self._apply_with_quorum(
                review, decision, actor_id=actor_id, actor_handle=actor_handle,
            )
        else:
            await self._apply(review, decision)
        return review, decision

    async def apply_decision_via_api(
        self, *, org_id: OrgId, review_id: ReviewId,
        kind: DecisionKind, feedback: str = "",
        excluded_platform_ids: list[str] | None = None,
    ) -> ReviewSession:
        review = await self.repo.get(org_id, review_id)
        if review is None:
            raise ValueError("review not found")
        # Persist exclusions on the session BEFORE applying the decision —
        # ``resume_after_review`` reads ``review.excluded_platform_ids``
        # when computing the publish set, so the order matters. We don't
        # union with prior exclusions: the latest decision is the source
        # of truth (user can change their mind by submitting again).
        if excluded_platform_ids is not None:
            review.excluded_platform_ids = list(excluded_platform_ids)
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

    async def _apply_with_quorum(
        self,
        review: ReviewSession,
        decision: ReviewDecision,
        *,
        actor_id: str,
        actor_handle: str | None,
    ) -> None:
        """Group-channel decision flow.

        Vote rules:
          • Reject vote → instant veto, no quorum needed.
          • Revise vote → instant pull-back; agents revise, new round opens.
          • Approve vote → counts toward ``quorum_required``; promotes the
            session only when the threshold is reached.
        Per-actor dedupe: tapping the same button twice is a no-op for the
        tally; switching from Approve → Revise updates that actor's vote.
        """
        kind_map = {
            DecisionKind.APPROVE: "approve",
            DecisionKind.REVISE:  "revise",
            DecisionKind.REJECT:  "reject",
        }
        vote_kind = kind_map.get(decision.kind)
        if vote_kind is None:
            return                                          # UNKNOWN button
        tally = review.record_vote(
            actor_id=actor_id, actor_handle=actor_handle, kind=vote_kind,
        )
        log.info(
            "review_vote_recorded",
            review_id=str(review.id), actor_id=actor_id,
            kind=vote_kind, tally=f"{tally.approve}/{tally.quorum_required}",
        )
        # Audit-log every vote — useful for editorial-board accountability.
        try:
            from app.api.deps import get_audit_log_service
            audit = get_audit_log_service()
            if audit is not None:
                await audit.record(
                    org_id=review.org_id,
                    actor_type="reviewer",
                    actor_id=None,           # actor_id here is a Telegram id, not a UUID
                    action=f"review.vote.{vote_kind}",
                    resource_type="review_session",
                    resource_id=review.id,
                    after={
                        "actor_telegram_id": actor_id,
                        "actor_handle": actor_handle,
                        "tally": {
                            "approve": tally.approve,
                            "revise": tally.revise,
                            "reject": tally.reject,
                            "quorum_required": tally.quorum_required,
                        },
                    },
                )
        except Exception:                                   # noqa: BLE001
            pass

        # Decide whether the session has hit a terminal state.
        if tally.reject_wins:
            review.reject(f"Vetoed by {actor_handle or actor_id}")
        elif tally.revise_wins:
            review.request_revision(
                decision.feedback or f"Revisions requested by {actor_handle or actor_id}",
            )
        elif tally.approve_wins:
            review.approve()
        # else: still pending — wait for more taps.
        await self.repo.update(review)

    async def acknowledge(self, review: ReviewSession, message: str) -> None:
        try:
            adapter = self.channel_adapter(review.channel)
            await adapter.acknowledge(review.recipient, message)
        except Exception as exc:                            # noqa: BLE001
            log.warning("review_ack_failed", error=str(exc))

    async def list_open(self, org_id: OrgId) -> list[ReviewSession]:
        return await self.repo.list_open(org_id)
