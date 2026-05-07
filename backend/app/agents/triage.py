"""TriageAgent — classifies inbound engagement and drafts replies.

Used by the EngagementService after polling each platform's comments/DMs.
The drafted reply is routed through the existing Review channel pipeline,
so a human approves before the bot ever speaks publicly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.adapters.engagement.base import (
    EngagementItem,
    EngagementSentiment,
)
from app.adapters.llm.base import LLMProvider, LLMRequest
from app.core.logging import get_logger

log = get_logger(__name__)

TRIAGE_SYSTEM = """You are the TriageAgent for a social-media account.
Given an inbound comment / DM / mention, decide:
1. sentiment (positive | neutral | negative | question | spam)
2. priority (low | normal | high)  — questions, complaints, sales leads → high
3. should_reply (true | false)     — false for spam / generic 🔥-only reactions
4. draft_reply  (string)           — empty if should_reply=false
                                     keep replies under 280 chars, brand tone

Return ONLY valid JSON: { "sentiment": ..., "priority": ..., "should_reply": ..., "draft_reply": "..." }
"""


@dataclass(frozen=True, slots=True)
class TriageResult:
    item: EngagementItem
    sentiment: EngagementSentiment
    priority: str           # low | normal | high
    should_reply: bool
    draft_reply: str
    rationale: str = ""


class TriageAgent:
    name = "triage"

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def classify(self, item: EngagementItem, brand_voice: str = "") -> TriageResult:
        prompt = (
            f"Platform: {item.plugin_name}\n"
            f"Kind: {item.kind.value}\n"
            f"Author: {item.author_handle or '(unknown)'}\n"
            f"Body: {item.body}\n"
            f"{('Brand voice notes: ' + brand_voice) if brand_voice else ''}"
        )
        rsp = await self.llm.complete(LLMRequest(
            prompt=prompt, system=TRIAGE_SYSTEM,
            response_format="json", temperature=0.2, max_tokens=300,
        ))
        data = _parse(rsp.text)
        return TriageResult(
            item=item,
            sentiment=_safe_enum(data.get("sentiment"), EngagementSentiment),
            priority=str(data.get("priority", "normal")),
            should_reply=bool(data.get("should_reply", True)),
            draft_reply=str(data.get("draft_reply", "")).strip(),
            rationale=str(data.get("rationale", "")),
        )


def _safe_enum(value, enum_cls):
    try:
        return enum_cls(value)
    except (ValueError, TypeError):
        return enum_cls.NEUTRAL if hasattr(enum_cls, "NEUTRAL") else next(iter(enum_cls))


def _parse(raw: str) -> dict:
    if not raw:
        return {}
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return {}
