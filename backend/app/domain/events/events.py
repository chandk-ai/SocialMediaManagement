from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base class for every domain event."""
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True, slots=True)
class WorkflowCreated(DomainEvent):
    workflow_id: UUID = None  # type: ignore[assignment]
    org_id: UUID = None       # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class WorkflowRunStarted(DomainEvent):
    run_id: UUID = None       # type: ignore[assignment]
    workflow_id: UUID = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class WorkflowRunFinished(DomainEvent):
    run_id: UUID = None       # type: ignore[assignment]
    workflow_id: UUID = None  # type: ignore[assignment]
    status: str = "succeeded"


@dataclass(frozen=True, slots=True)
class PostGenerated(DomainEvent):
    post_id: UUID = None      # type: ignore[assignment]
    platform_id: UUID = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class PostPublished(DomainEvent):
    post_id: UUID = None      # type: ignore[assignment]
    external_post_id: str = ""


@dataclass(frozen=True, slots=True)
class PostFailed(DomainEvent):
    post_id: UUID = None      # type: ignore[assignment]
    error: str = ""


@dataclass(frozen=True, slots=True)
class PlatformConnected(DomainEvent):
    platform_id: UUID = None  # type: ignore[assignment]
    plugin_name: str = ""


@dataclass(frozen=True, slots=True)
class PlatformDisconnected(DomainEvent):
    platform_id: UUID = None  # type: ignore[assignment]
