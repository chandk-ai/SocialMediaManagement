"""Critique — decides approve / revise / escalate based on evaluations."""
from __future__ import annotations

from .base import Agent, AgentDecision, AgentState


class CritiqueAgent(Agent):
    name = "critique"

    async def run(self, state: AgentState) -> AgentState:
        cfg = state.workflow_config
        if not state.evaluations:
            return state.merge(decision=AgentDecision.ABORT)

        # Hard compliance gate first
        any_flags = any(e.flags for e in state.evaluations)
        if any_flags:
            notes = ["Compliance flags raised — escalate to human."]
            for ev in state.evaluations:
                notes.extend(ev.flags)
            state.log(self.name, "decision", decision="escalate", reason="flags")
            return state.merge(decision=AgentDecision.ESCALATE, critique_notes=notes)

        worst = min(e.overall for e in state.evaluations)
        if worst >= cfg.quality_threshold:
            state.log(self.name, "decision", decision="approve", worst=worst)
            return state.merge(decision=AgentDecision.APPROVE, critique_notes=[])

        if (
            worst >= cfg.low_quality_threshold
            and state.revision_count < cfg.max_revisions
        ):
            notes = []
            for d, e in zip(state.drafts, state.evaluations, strict=False):
                if e.overall < cfg.quality_threshold:
                    notes.append(
                        f"[{d.platform_name}] score={e.overall:.2f}; "
                        f"suggestions: {'; '.join(e.suggestions) or 'tighten the hook'}"
                    )
            state.log(
                self.name, "decision", decision="revise",
                worst=worst, revision=state.revision_count + 1,
            )
            return state.merge(
                decision=AgentDecision.REVISE,
                critique_notes=notes,
                revision_count=state.revision_count + 1,
            )

        state.log(self.name, "decision", decision="escalate",
                  worst=worst, revisions=state.revision_count)
        return state.merge(
            decision=AgentDecision.ESCALATE,
            critique_notes=[f"Quality stayed below threshold after {state.revision_count} revisions"],
        )
