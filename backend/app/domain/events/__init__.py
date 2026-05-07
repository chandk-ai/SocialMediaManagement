"""Domain events. Published by entities/services and consumed by handlers
(notifications, audit log, analytics) via the in-process event bus or the
transactional outbox for cross-service propagation.
"""
from .events import (
    DomainEvent,
    WorkflowCreated,
    WorkflowRunStarted,
    WorkflowRunFinished,
    PostGenerated,
    PostPublished,
    PostFailed,
    PlatformConnected,
    PlatformDisconnected,
)

__all__ = [
    "DomainEvent",
    "WorkflowCreated", "WorkflowRunStarted", "WorkflowRunFinished",
    "PostGenerated", "PostPublished", "PostFailed",
    "PlatformConnected", "PlatformDisconnected",
]
