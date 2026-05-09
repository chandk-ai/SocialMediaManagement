"""Job-handler registry.

Handlers are async callables registered by name (matching JobRecord.kind).
The worker loop dispatches a claimed job to the handler, awaiting it
inside a try/except. Exceptions translate to:

    * PermanentError → fail(permanent=True) — DLQ immediately, no retry.
    * Anything else  → fail(error=…) — bumps attempt, exponential backoff.
    * Returns dict   → complete(result=dict)

Handlers receive a HandlerContext that carries the JobRecord, the queue
itself (so a handler can enqueue a follow-up phase), and lazily-built
service handles. Lazy is important: workers don't need to construct
every service if a job only touches one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.services.jobs.queue import JobQueue, JobRecord


HandlerFn = Callable[["HandlerContext"], Awaitable[dict[str, Any] | None]]


@dataclass(slots=True)
class HandlerContext:
    job: JobRecord
    queue: JobQueue
    services: dict[str, Any]


REGISTRY: dict[str, HandlerFn] = {}


def register_handler(kind: str):
    """Decorator: ``@register_handler('run.publish')`` adds the handler
    to the dispatch table. Idempotent so reloads don't duplicate."""
    def deco(fn: HandlerFn) -> HandlerFn:
        REGISTRY[kind] = fn
        return fn
    return deco
