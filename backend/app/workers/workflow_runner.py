"""Celery tasks that drive the workflow pipeline asynchronously.

Post-Pillar-1, ``run_workflow`` is a thin shim that *enqueues* the run
on the durable queue and returns immediately. The actual phases
(select / plan / tailor / execute / critique / publish) execute on the
``smms-jobs-worker`` service via the Postgres-backed jobs table —
giving us retries, circuit breakers, and crash-resilience that Celery
alone never provided for inline runs.

The Celery worker still owns:
  * ``publish_post``   re-publish escape hatch (legacy DLQ path)
  * Other periodic tasks defined elsewhere in app.workers

The Celery beat scheduler hits this task on each scheduled-workflow
firing. From the customer's point of view nothing changes — the run
still happens; the difference is that a worker crash mid-execution
no longer abandons the run.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.logging import get_logger
from app.infrastructure.queue.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="app.workers.workflow_runner.run_workflow",
                 autoretry_for=(Exception,),
                 retry_backoff=True, retry_backoff_max=300,
                 retry_jitter=True, max_retries=3)
def run_workflow(org_id: str, workflow_id: str,
                  trigger_kind: str = "schedule",
                  directive: str | None = None) -> str:
    """Enqueue the workflow run on the durable jobs queue. Returns the
    enqueued ``run.start`` job_id (NOT a run_id — the run row is created
    inside the run.start handler when a worker picks it up).

    Idempotency: keyed on (workflow_id, calling_minute) so a Celery
    retry after a transient broker error doesn't double-enqueue."""
    log.info("celery_enqueue_run", workflow_id=workflow_id,
             trigger_kind=trigger_kind)

    async def _enqueue():
        from app.api.deps import get_job_queue
        from app.services.jobs.queue import DuplicateIdempotencyKey
        import time
        q = get_job_queue()
        # Round to the minute so retries within the same Celery task
        # invocation collapse, but a *new* schedule firing 60s later
        # still creates a fresh job.
        bucket = int(time.time() // 60)
        try:
            job = await q.enqueue(
                "run.start", org_id,
                {"workflow_id": str(workflow_id),
                 "trigger_kind": trigger_kind,
                 "directive": directive},
                idempotency_key=f"celery-start:{workflow_id}:{bucket}",
            )
            return job.id
        except DuplicateIdempotencyKey as exc:
            log.info("celery_enqueue_dedup",
                     existing=exc.existing_job_id)
            return exc.existing_job_id

    return asyncio.run(_enqueue())


@celery_app.task(name="app.workers.workflow_runner.publish_post",
                 autoretry_for=(Exception,),
                 retry_backoff=True, max_retries=5)
def publish_post(org_id: str, post_id: str) -> bool:
    """Re-publish a previously failed or scheduled post."""
    log.info("worker_publish_post", post_id=post_id)
    # Concrete implementation calls PostService.publish(...)
    return True
