"""Review channel — outbound side of the human-in-the-loop checkpoint.

When the orchestrator decides drafts need approval, it asks the configured
ReviewChannel to deliver them to the reviewer (WhatsApp DM, Instagram DM,
email, Slack DM, or the in-app queue) and returns the channel-specific
message ID so we can correlate the eventual reply back to the ReviewSession.

The reverse path — *ingesting* the reviewer's reply — happens through the
matching `TriggerAdapter` (e.g. `WhatsAppTrigger`) plus the helper
`parse_decision()` defined here, which interprets natural-language replies
("yes", "approve", "no, make it more casual", ...) into a structured
`ReviewDecision`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar


class DecisionKind(str, Enum):
    APPROVE = "approve"
    REVISE  = "revise"
    REJECT  = "reject"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    kind: DecisionKind
    feedback: str = ""
    raw_text: str = ""


@dataclass(frozen=True, slots=True)
class ReviewMessage:
    """The packaged draft we send to the reviewer."""
    headline: str
    drafts: list[dict]                 # serialised DraftPost rows
    actions: list[str] = field(default_factory=lambda: ["✅ Approve", "✏️ Revise", "❌ Reject"])
    metadata: dict[str, Any] = field(default_factory=dict)


class ReviewChannel(ABC):
    plugin_name: ClassVar[str] = ""
    api_version: ClassVar[str] = "1.0"
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    config_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    async def send_for_review(self, recipient: str, message: ReviewMessage) -> str:
        """Deliver the draft. Returns the *channel-side* message id used to
        correlate the reviewer's eventual reply back to the ReviewSession.
        """

    async def acknowledge(
        self, recipient: str, text: str, *, request_reply: bool = False,
    ) -> None:
        """Optional follow-up message ("Posted!", "Got it, revising...").

        ``request_reply`` is a hint that the bot is asking the user for
        free-text input (e.g. "what should change?"). Channels that
        support an interactive reply UI (Telegram's ``force_reply``)
        should honor it; channels that don't (email, in_app) treat
        the message as a plain notification and the user replies
        through whatever interface they normally use.
        """


# ── decision parsing ───────────────────────────────────────────────────────
APPROVE_KEYWORDS = {
    "yes", "y", "ok", "okay", "approve", "approved", "post", "post it",
    "publish", "looks good", "lgtm", "ship it", "go", "do it", "ship", "✅",
}
REJECT_KEYWORDS = {
    "no", "n", "reject", "cancel", "stop", "don't", "do not", "kill", "❌",
}
REVISE_KEYWORDS = {
    "revise", "edit", "change", "rework", "tweak", "fix", "different",
    "more casual", "more formal", "shorter", "longer", "✏️",
}


def parse_decision(reply_text: str) -> ReviewDecision:
    """Interpret a reviewer's natural-language reply.

    Heuristics, in order:
        1. Exact keyword match → APPROVE / REJECT
        2. Reply *starts with* a revise keyword → REVISE + remainder as feedback
        3. Anything else and longer than 4 words → REVISE (treat as feedback)
        4. Else UNKNOWN — caller should re-prompt.
    """
    raw = (reply_text or "").strip()
    lo = raw.lower()
    if lo in APPROVE_KEYWORDS:
        return ReviewDecision(DecisionKind.APPROVE, raw_text=raw)
    if lo in REJECT_KEYWORDS:
        return ReviewDecision(DecisionKind.REJECT, raw_text=raw)
    for kw in REVISE_KEYWORDS:
        if lo.startswith(kw):
            feedback = raw[len(kw):].lstrip(" :,-").strip()
            return ReviewDecision(DecisionKind.REVISE, feedback=feedback or raw, raw_text=raw)
    if any(kw in lo for kw in REVISE_KEYWORDS):
        return ReviewDecision(DecisionKind.REVISE, feedback=raw, raw_text=raw)
    if len(raw.split()) >= 4:
        # treat substantive free text as a revision request
        return ReviewDecision(DecisionKind.REVISE, feedback=raw, raw_text=raw)
    return ReviewDecision(DecisionKind.UNKNOWN, raw_text=raw)
