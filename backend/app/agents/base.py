"""Common agent abstractions.

`AgentState` is the immutable record passed between graph nodes. Every agent
returns a new state via `state.merge(...)`. We deliberately keep this in
plain dataclasses so the orchestrator stays framework-agnostic — LangGraph
adapts to it (see orchestrator.py)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from app.adapters.llm.base import LLMProvider
from app.domain.entities.source import SourceItem
from app.domain.entities.workflow import WorkflowConfig
from app.domain.value_objects.content import (
    ContentPlan,
    DraftPost,
    EvaluationReport,
    PostBlueprint,
)


class AgentDecision(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    ESCALATE = "escalate"      # human review
    ABORT = "abort"


@dataclass(slots=True)
class AgentState:
    workflow_config: WorkflowConfig
    target_platforms: list[str]                                 # plugin names
    source_items: list[SourceItem] = field(default_factory=list)
    plan: ContentPlan | None = None
    drafts: list[DraftPost] = field(default_factory=list)
    evaluations: list[EvaluationReport] = field(default_factory=list)
    revision_count: int = 0
    decision: AgentDecision | None = None
    critique_notes: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    # Free-text instruction supplied by the trigger (e.g. an inbound WhatsApp
    # message). Empty for pure source-driven runs.
    directive: str = ""
    # Brand-voice retrieval block, populated by the orchestrator when the
    # workflow opts in. Splice into Executor prompts to anchor generation
    # against past high-performing posts.
    voice_block: str = ""

    def merge(self, **changes: Any) -> "AgentState":
        return replace(self, **changes)

    def log(self, agent: str, event: str, **payload: Any) -> None:
        self.trace.append({
            "agent": agent, "event": event,
            "ts": datetime.utcnow().isoformat(),
            **payload,
        })


class Agent(ABC):
    name: str = "agent"

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    @abstractmethod
    async def run(self, state: AgentState) -> AgentState: ...

    # Helpers shared by all agents
    @staticmethod
    def _post_blueprint_for(platform: str, plan: ContentPlan) -> PostBlueprint | None:
        return next((b for b in plan.blueprints if b.platform_name == platform), None)
