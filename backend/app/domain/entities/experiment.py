"""Experiment aggregate — A/B/n variant testing for posts.

The agent generates `n` variants per draft. Each variant is shipped to a
distinct cohort (or as a holdout split). After a configurable settling
window we compute a winner from `Post.metrics` and persist a "winning
template" that can be reused by future runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..value_objects.ids import (
    ExperimentId,
    OrgId,
    PlatformId,
    PostId,
    VariantId,
    WorkflowId,
    new_id,
)


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class VariantStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    WINNER = "winner"
    LOSER = "loser"
    FAILED = "failed"


class AllocationKind(str, Enum):
    """How traffic is split across variants."""
    EQUAL = "equal"            # 50/50 (or 1/n)
    HOLDOUT = "holdout"        # one variant is control, others get majority
    BANDIT = "bandit"          # epsilon-greedy adaptive (future-proof flag)


@dataclass(slots=True)
class Variant:
    id: VariantId
    label: str                              # "A", "B", "control"
    text: str
    hashtags: tuple[str, ...] = ()
    media_briefs: tuple[str, ...] = ()
    weight: float = 1.0                     # fraction of cohort (sum ≈ 1.0)
    post_id: PostId | None = None           # populated when published
    status: VariantStatus = VariantStatus.PENDING
    metric_value: float | None = None
    notes: str | None = None
    extras: dict = field(default_factory=dict)

    def mark_published(self, post_id: PostId) -> None:
        self.post_id = post_id
        self.status = VariantStatus.PUBLISHED

    def record_metric(self, value: float) -> None:
        self.metric_value = value

    @classmethod
    def create(
        cls,
        *,
        label: str,
        text: str,
        hashtags: list[str] | None = None,
        media_briefs: list[str] | None = None,
        weight: float = 1.0,
        extras: dict | None = None,
    ) -> "Variant":
        return cls(
            id=VariantId(new_id()),
            label=label,
            text=text,
            hashtags=tuple(hashtags or ()),
            media_briefs=tuple(media_briefs or ()),
            weight=weight,
            extras=dict(extras or {}),
        )


@dataclass(slots=True)
class Experiment:
    id: ExperimentId
    org_id: OrgId
    workflow_id: WorkflowId
    platform_id: PlatformId
    hypothesis: str
    metric: str = "engagement_rate"          # which Post.metrics key to compare
    settling_minutes: int = 60 * 24          # 24h default before deciding
    allocation: AllocationKind = AllocationKind.EQUAL
    variants: list[Variant] = field(default_factory=list)
    status: ExperimentStatus = ExperimentStatus.DRAFT
    started_at: datetime | None = None
    settled_at: datetime | None = None
    winner_variant_id: VariantId | None = None
    extras: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def start(self) -> None:
        if not self.variants or len(self.variants) < 2:
            raise ValueError("Experiment requires at least 2 variants")
        self.status = ExperimentStatus.RUNNING
        self.started_at = datetime.utcnow()
        self.updated_at = self.started_at

    def is_settling_complete(self, now: datetime) -> bool:
        if self.status is not ExperimentStatus.RUNNING or not self.started_at:
            return False
        return now >= self.started_at + timedelta(minutes=self.settling_minutes)

    def settle(self) -> Variant | None:
        """Pick the winning variant by `metric` and persist the decision."""
        published = [
            v for v in self.variants
            if v.status is VariantStatus.PUBLISHED and v.metric_value is not None
        ]
        if not published:
            self.status = ExperimentStatus.SETTLED
            self.settled_at = datetime.utcnow()
            self.updated_at = self.settled_at
            return None
        winner = max(published, key=lambda v: v.metric_value or 0.0)
        for v in self.variants:
            if v.id == winner.id:
                v.status = VariantStatus.WINNER
            elif v.status is VariantStatus.PUBLISHED:
                v.status = VariantStatus.LOSER
        self.winner_variant_id = winner.id
        self.status = ExperimentStatus.SETTLED
        self.settled_at = datetime.utcnow()
        self.updated_at = self.settled_at
        return winner

    def cancel(self, reason: str | None = None) -> None:
        self.status = ExperimentStatus.CANCELLED
        if reason:
            self.extras = {**self.extras, "cancellation_reason": reason}
        self.updated_at = datetime.utcnow()

    @classmethod
    def create(
        cls,
        *,
        org_id: OrgId,
        workflow_id: WorkflowId,
        platform_id: PlatformId,
        hypothesis: str,
        variants: list[Variant],
        metric: str = "engagement_rate",
        settling_minutes: int = 60 * 24,
        allocation: AllocationKind = AllocationKind.EQUAL,
        extras: dict | None = None,
    ) -> "Experiment":
        return cls(
            id=ExperimentId(new_id()),
            org_id=org_id,
            workflow_id=workflow_id,
            platform_id=platform_id,
            hypothesis=hypothesis,
            variants=list(variants),
            metric=metric,
            settling_minutes=settling_minutes,
            allocation=allocation,
            extras=dict(extras or {}),
        )
