from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..value_objects.ids import OrgId, PlatformId, SourceId, WorkflowId, new_id
from ..value_objects.schedule import Schedule
from ..value_objects.targeting import TargetSelector


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


@dataclass(slots=True)
class WorkflowConfig:
    """Declarative configuration for an agent pipeline."""
    tone: str = "professional"
    audience: str = "general"
    voice_guide: str | None = None
    max_revisions: int = 3
    quality_threshold: float = 0.75
    low_quality_threshold: float = 0.5
    require_human_approval: bool = False
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    # Brand-voice RAG: when True the Executor pulls top-K high-performing
    # past posts for the same platform and adds them to the prompt.
    use_brand_voice: bool = False
    brand_voice_top_k: int = 5
    extra: dict = field(default_factory=dict)


@dataclass(slots=True)
class Workflow:
    """The recipe: which sources feed which platforms, on what schedule."""
    id: WorkflowId
    org_id: OrgId
    name: str
    description: str
    source_ids: list[SourceId]
    platform_ids: list[PlatformId]    # explicit fallback target list
    config: WorkflowConfig
    schedule: Schedule
    # Default declarative routing — what gets used if a per-run directive
    # doesn't override it. Empty selector → fall back to platform_ids.
    target_selector: TargetSelector = field(default_factory=TargetSelector)
    status: WorkflowStatus = WorkflowStatus.DRAFT
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def activate(self) -> None:
        # Sources are optional — a trigger-driven workflow can run on the
        # user's directive alone. Platforms (or a non-empty selector) are
        # required because we need somewhere to publish.
        if not self.platform_ids and self.target_selector.is_empty():
            raise ValueError("Cannot activate a workflow with no target platforms")
        self.status = WorkflowStatus.ACTIVE
        self.updated_at = datetime.utcnow()

    def pause(self) -> None:
        self.status = WorkflowStatus.PAUSED
        self.updated_at = datetime.utcnow()

    @classmethod
    def create(
        cls, *, org_id: OrgId, name: str, description: str,
        source_ids: list[SourceId], platform_ids: list[PlatformId],
        config: WorkflowConfig, schedule: Schedule,
        target_selector: TargetSelector | None = None,
    ) -> "Workflow":
        return cls(
            id=WorkflowId(new_id()),
            org_id=org_id,
            name=name,
            description=description,
            source_ids=source_ids,
            platform_ids=platform_ids,
            config=config,
            schedule=schedule,
            target_selector=target_selector or TargetSelector(),
        )
