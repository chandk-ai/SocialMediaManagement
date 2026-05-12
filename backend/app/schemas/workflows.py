from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .common import APIModel


class WorkflowConfigIn(APIModel):
    tone: str = "professional"
    audience: str = "general"
    voice_guide: str | None = None
    max_revisions: int = 3
    quality_threshold: float = 0.75
    low_quality_threshold: float = 0.5
    require_human_approval: bool = False
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    use_brand_voice: bool = False
    brand_voice_top_k: int = 5
    # Niche #4 — compliance profile name (finra / hipaa / fda / crypto / gdpr).
    # None or "none" disables the scan.
    compliance_profile: str | None = None
    # Power-user override: appended to the Planner + Executor system
    # prompts. None / empty = no override.
    custom_system_prompt: str | None = None
    # Pluggable item-selection (Niche #101). plugin name + per-strategy
    # config (must conform to that strategy's ``config_schema``).
    selection_strategy: str = "freshness"
    selection_config: dict = Field(default_factory=dict)
    extra: dict = Field(default_factory=dict)


class ScheduleIn(APIModel):
    kind: str = Field(..., examples=["cron", "interval", "once", "manual"])
    cron: str | None = None
    interval_minutes: int | None = None
    run_at: datetime | None = None
    timezone: str = "UTC"


class TargetSelectorIn(APIModel):
    explicit_platform_ids: list[UUID] = Field(default_factory=list)
    all_of_platforms: list[str] = Field(
        default_factory=list, examples=[["instagram", "facebook"]],
    )
    by_handle: list[str] = Field(default_factory=list, examples=[["@brand", "@careers"]])
    tagged: list[str] = Field(default_factory=list, examples=[["marketing"]])
    exclude_platform_ids: list[UUID] = Field(default_factory=list)
    exclude_plugins: list[str] = Field(default_factory=list)


class WorkflowCreate(APIModel):
    name: str
    description: str = ""
    source_ids: list[UUID]
    platform_ids: list[UUID]
    config: WorkflowConfigIn = Field(default_factory=WorkflowConfigIn)
    schedule: ScheduleIn
    target_selector: TargetSelectorIn = Field(default_factory=TargetSelectorIn)


class WorkflowOut(APIModel):
    id: UUID
    name: str
    description: str
    status: str
    source_ids: list[UUID]
    platform_ids: list[UUID]
    config: WorkflowConfigIn
    schedule: ScheduleIn
    target_selector: TargetSelectorIn = Field(default_factory=TargetSelectorIn)
    created_at: datetime
    updated_at: datetime
    # Surfacing last_fired_at on the API so the workflow card can show
    # "Last run X" accurately for scheduled runs (today this only
    # populates after a Manual or post-deploy scheduled fire).
    last_fired_at: datetime | None = None


class WorkflowRunOut(APIModel):
    id: UUID
    workflow_id: UUID
    status: str
    revision_count: int
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    trace: list[dict]
