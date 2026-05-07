"""Evaluator — scores each DraftPost across the rubric dimensions."""
from __future__ import annotations

import asyncio
import json
import re

from app.adapters.llm.base import LLMRequest
from app.domain.value_objects.content import EvaluationReport

from .base import Agent, AgentState

EVALUATOR_SYSTEM = """You are the Evaluator agent. Score the supplied draft post
on five dimensions, each in [0.0, 1.0]:
- clarity        : is the message clear and well-structured?
- brand_voice    : does it match the requested tone?
- compliance     : free of banned words, sensitive topics, false claims?
- platform_fit   : appropriate length, hashtags, format for the platform?
- predicted_engagement : likelihood of likes/shares/comments

Also produce:
- "flags" : a list of compliance/legal flags (empty if none)
- "suggestions" : concrete improvements (max 3)

Return ONLY valid JSON: {
 "clarity": float, "brand_voice": float, "compliance": float,
 "platform_fit": float, "predicted_engagement": float,
 "flags": [string], "suggestions": [string]
}
"""

BANNED_TERMS = ("guaranteed returns", "miracle cure", "free money")


class EvaluatorAgent(Agent):
    name = "evaluator"

    async def run(self, state: AgentState) -> AgentState:
        if not state.drafts:
            state.log(self.name, "no_drafts")
            return state

        async def _score_one(draft) -> EvaluationReport:
            heuristic_flags = _heuristic_flags(draft.text)
            prompt = (
                f"Platform: {draft.platform_name}\n"
                f"Tone: {state.workflow_config.tone}\n"
                f"Voice guide: {state.workflow_config.voice_guide or '(none)'}\n\n"
                f"Draft:\n{draft.text}"
            )
            rsp = await self.llm.complete(LLMRequest(
                prompt=prompt, system=EVALUATOR_SYSTEM,
                response_format="json", temperature=0.0, max_tokens=400,
            ))
            scores = _parse_scores(rsp.text)
            scores["flags"] = list({*scores.get("flags", []), *heuristic_flags})
            return EvaluationReport.from_scores(
                {k: scores[k] for k in (
                    "clarity", "brand_voice", "compliance", "platform_fit", "predicted_engagement"
                )},
                suggestions=scores.get("suggestions", []),
                flags=scores.get("flags", []),
            )

        evals = await asyncio.gather(*(_score_one(d) for d in state.drafts))
        state.log(
            self.name, "scored",
            avg=sum(e.overall for e in evals) / len(evals),
            min=min(e.overall for e in evals),
        )
        return state.merge(evaluations=list(evals))


def _parse_scores(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return _default_scores()
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return _default_scores()
    out = _default_scores()
    for k in ("clarity", "brand_voice", "compliance", "platform_fit", "predicted_engagement"):
        v = d.get(k, 0.7)
        out[k] = max(0.0, min(1.0, float(v)))
    out["flags"] = list(d.get("flags", []))
    out["suggestions"] = list(d.get("suggestions", []))
    return out


def _default_scores() -> dict:
    return {
        "clarity": 0.7, "brand_voice": 0.7, "compliance": 1.0,
        "platform_fit": 0.7, "predicted_engagement": 0.6,
        "flags": [], "suggestions": [],
    }


def _heuristic_flags(text: str) -> list[str]:
    lo = text.lower()
    return [t for t in BANNED_TERMS if t in lo]
