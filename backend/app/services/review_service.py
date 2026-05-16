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
from app.repositories.ports import ReviewSessionRepository, TriggerRepository

log = get_logger(__name__)


def _guess_kind(url: str) -> str:
    """Best-effort MediaKind guess from a URL extension. Defaults to
    ``image`` because that's by far the most common reviewer attachment.
    Used when the inbound channel (Telegram, WhatsApp, …) doesn't tell
    us whether the file is image / video / audio in a structured way."""
    u = (url or "").split("?", 1)[0].lower()
    for ext in (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"):
        if u.endswith(ext):
            return "video"
    return "image"


class ReviewService:
    def __init__(
        self,
        repo: ReviewSessionRepository,
        registry: PluginRegistry,
        *,
        trigger_repo: TriggerRepository | None = None,
    ) -> None:
        self.repo = repo
        self.registry = registry
        # Optional — when supplied, ``acknowledge`` can look up the
        # active trigger that owns the bot_token (etc.) for this
        # review's channel. Without it, channel adapters only see
        # env-var config which is wrong for multi-tenant / multi-bot
        # deployments (the per-trigger token never gets injected and
        # the Telegram call short-circuits as a dry-run). See the
        # mirror lookup in WorkflowService._dispatch_review_message
        # for the canonical pattern.
        self.trigger_repo = trigger_repo

    def channel_adapter(self, name: str, config: dict | None = None) -> ReviewChannel:
        entry = self.registry.get(PluginKind.REVIEW_CHANNEL, name)
        return entry.cls(config=config or {})

    async def _channel_config_for_review(self, review: ReviewSession) -> dict:
        """Find the active Trigger whose review_channel matches the
        review's channel and return its config (which carries the
        bot_token / phone_number_id / etc.). Empty dict on miss —
        adapters can still fall back to env-var config.

        Mirrors the lookup used by ``WorkflowService._dispatch_review_message``
        so outbound dispatch and inbound acknowledgements share the
        exact same credentials source. Without this, an acknowledge
        call (Revise "What should change?" prompt) silently dry-runs
        because the adapter sees an empty token."""
        if self.trigger_repo is None:
            return {}
        try:
            triggers = await self.trigger_repo.list(review.org_id)
            candidate = next(
                (t for t in triggers
                 if t.workflow_id == review.workflow_id
                 and t.is_active
                 and t.review_channel == review.channel),
                None,
            )
            if candidate is None:
                # Fallback — any active trigger on this workflow that
                # uses the same channel. Covers edge cases where the
                # review_channel field on the trigger was set after
                # the review was created.
                candidate = next(
                    (t for t in triggers
                     if t.workflow_id == review.workflow_id
                     and t.is_active
                     and (t.config or {}).get("bot_token")),
                    None,
                )
            return dict(candidate.config or {}) if candidate is not None else {}
        except Exception as exc:                                # noqa: BLE001
            log.info("review_ack_trigger_lookup_failed",
                     review_id=str(review.id), error=str(exc))
            return {}

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
        feedback_media: list[str] | None = None,
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
        # Persist any reviewer-supplied media (e.g. an image attached
        # to a Telegram reply with revision feedback). Store BEFORE
        # applying the decision so resume_after_review sees a fully
        # populated session when it reads back.
        if feedback_media:
            review.feedback_media = [
                {"url": u, "kind": _guess_kind(u), "alt_text": None}
                for u in feedback_media if u
            ]
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

    async def acknowledge(
        self, review: ReviewSession, message: str,
        *, request_reply: bool = False,
    ) -> None:
        """Send an out-of-band follow-up on the review's channel.

        ``request_reply=True`` asks the channel adapter to render the
        message with an interactive reply UI (Telegram's
        ``force_reply``). Used when the Revise button is tapped — the
        bot needs the user's typed feedback before the agent re-run
        is useful.

        The trigger row that owns this review's channel carries the
        bot_token / phone_number_id / access_token; we look it up and
        pass it to the channel adapter. Without this step the adapter
        sees an empty config, can't read a token from env vars in a
        multi-tenant deploy, and falls through to its ``dry_run``
        path — meaning the user never receives the prompt. (Render
        logs: ``telegram_ack_dry_run`` events appearing exactly when
        the user taps Revise.)
        """
        try:
            channel_config = await self._channel_config_for_review(review)
            adapter = self.channel_adapter(review.channel, channel_config)
            await adapter.acknowledge(
                review.recipient, message, request_reply=request_reply,
            )
            log.info(
                "review_ack_sent",
                review_id=str(review.id),
                channel=review.channel,
                recipient=review.recipient,
                request_reply=request_reply,
                config_has_token=bool(
                    channel_config.get("bot_token")
                    or channel_config.get("access_token")
                    or channel_config.get("phone_number_id")
                ),
            )
        except Exception as exc:                            # noqa: BLE001
            log.warning("review_ack_failed",
                        review_id=str(review.id), error=str(exc))

    async def list_open(self, org_id: OrgId) -> list[ReviewSession]:
        return await self.repo.list_open(org_id)
