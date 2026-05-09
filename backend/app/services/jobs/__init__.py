"""Durable job queue + worker — the resilient backbone for runs.

The orchestrator no longer executes a workflow run inline. Instead each
phase (select / plan / tailor / execute / critique / publish) is enqueued
as a row in ``smms.jobs`` and consumed by the worker loop. This buys us:

* **Survives pod restarts** — an in-flight run's next phase is in the
  database, not in the Python heap.
* **Retries with backoff** — transient failures (502 from a platform,
  rate-limit, brief LLM timeout) bump ``attempt`` and re-schedule.
* **Dead-letter** — exhausted retries flip to ``status='dead'`` so an
  operator can see them on the admin job board without clogging
  the live queue.
* **Idempotency** — ``idempotency_key`` prevents the API from creating
  duplicate jobs on a retried POST.
* **Horizontal scale** — multiple workers can ``SKIP LOCKED`` over the
  same table without stepping on each other.

The contract is a small interface (``JobQueue``) so the memory backend
can satisfy it for tests / dev without Postgres.
"""
from app.services.jobs.queue import (
    JobQueue,
    JobRecord,
    JobStatus,
    JobNotFound,
    DuplicateIdempotencyKey,
)
from app.services.jobs.handlers import REGISTRY, register_handler
# Importing the handler modules registers them via decorator side effects.
from app.services.jobs import handlers_workflow  # noqa: F401
from app.services.jobs import handlers_engagement  # noqa: F401
from app.services.jobs import handlers_system  # noqa: F401

__all__ = [
    "JobQueue", "JobRecord", "JobStatus", "JobNotFound",
    "DuplicateIdempotencyKey", "REGISTRY", "register_handler",
]
