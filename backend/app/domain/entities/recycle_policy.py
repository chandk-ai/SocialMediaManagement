"""RecyclePolicy entity — drives the evergreen-republisher Celery job.

A `RecyclePolicy` says: "for posts that match this filter, republish them
on this cadence after this cooldown, optionally rewriting the copy via the
LLM to keep things fresh."

The actual recycling work happens in
`app/services/content_recycler.py` and `app/workers/content_recycler.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..value_objects.ids import (
    OrgId,
    PlatformId,
    RecyclePolicyId,
    WorkflowId,
    new_id,
)


class RecycleStrategy(str, Enum):
    REPOST_VERBATIM = "repost_verbatim"     # exact text, new schedule
    LIGHT_REWRITE = "light_rewrite"          # LLM rewrites for variety
    FULL_REGEN = "full_regen"                # treat as fresh draft, same brief
    THREAD_FROM_TOP = "thread_from_top"      # convert top performer → thread


@dataclass(slots=True)
class RecyclePolicy:
    id: RecyclePolicyId
    org_id: OrgId
    name: str
    strategy: RecycleStrategy = RecycleStrategy.LIGHT_REWRITE
    cooldown_days: int = 30                  # earliest a post can be recycled
    max_recycle_count: int = 3               # avoid infinite reuse
    cadence_days: int = 14                   # how often this policy runs
    min_engagement_score: float = 0.4        # only recycle high performers
    workflow_ids: tuple[WorkflowId, ...] = ()    # empty = all
    platform_ids: tuple[PlatformId, ...] = ()    # empty = all
    enabled: bool = True
    last_run_at: datetime | None = None
    extras: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def covers_workflow(self, workflow_id: WorkflowId | None) -> bool:
        if not self.workflow_ids:
            return True
        return workflow_id is not None and workflow_id in self.workflow_ids

    def covers_platform(self, platform_id: PlatformId | None) -> bool:
        if not self.platform_ids:
            return True
        return platform_id is not None and platform_id in self.platform_ids

    def is_due(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        if self.last_run_at is None:
            return True
        elapsed_days = (now - self.last_run_at).total_seconds() / 86400.0
        return elapsed_days >= self.cadence_days

    def mark_ran(self) -> None:
        self.last_run_at = datetime.utcnow()
        self.updated_at = self.last_run_at

    @classmethod
    def create(
        cls,
        *,
        org_id: OrgId,
        name: str,
        strategy: RecycleStrategy = RecycleStrategy.LIGHT_REWRITE,
        cooldown_days: int = 30,
        max_recycle_count: int = 3,
        cadence_days: int = 14,
        min_engagement_score: float = 0.4,
        workflow_ids: list[WorkflowId] | None = None,
        platform_ids: list[PlatformId] | None = None,
    ) -> "RecyclePolicy":
        return cls(
            id=RecyclePolicyId(new_id()),
            org_id=org_id,
            name=name,
            strategy=strategy,
            cooldown_days=cooldown_days,
            max_recycle_count=max_recycle_count,
            cadence_days=cadence_days,
            min_engagement_score=min_engagement_score,
            workflow_ids=tuple(workflow_ids or ()),
            platform_ids=tuple(platform_ids or ()),
        )
