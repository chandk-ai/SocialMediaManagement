"""Celery tasks that drive the workflow pipeline asynchronously."""
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
def run_workflow(org_id: str, workflow_id: str) -> str:
    """Kick off a workflow run. Returns the run ID."""
    log.info("worker_run_workflow", workflow_id=workflow_id)

    async def _exec():
        # Late import keeps the worker startup fast.
        from app.api.deps import build_dev_workflow_service
        svc = await build_dev_workflow_service()
        from app.domain.value_objects.ids import OrgId, WorkflowId
        run = await svc.run(OrgId(UUID(org_id)), WorkflowId(UUID(workflow_id)))
        return str(run.id)

    return asyncio.run(_exec())


@celery_app.task(name="app.workers.workflow_runner.publish_post",
                 autoretry_for=(Exception,),
                 retry_backoff=True, max_retries=5)
def publish_post(org_id: str, post_id: str) -> bool:
    """Re-publish a previously failed or scheduled post."""
    log.info("worker_publish_post", post_id=post_id)
    # Concrete implementation calls PostService.publish(...)
    return True
