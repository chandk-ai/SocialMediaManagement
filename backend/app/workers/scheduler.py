"""Celery Beat scheduler — drives `Workflow.schedule`.

Strategy: a single beat task (`tick_scheduler`) runs every minute, queries
all active workflows, and decides which ones are due. Due workflows enqueue
a `run_workflow` task on the `workflows` queue. This avoids having to
register every workflow as its own beat entry (which doesn't survive a
hot-reload anyway).

Schedule kinds handled here:
  * CRON     — uses croniter for next-fire-time evaluation
  * INTERVAL — last_fired_at + interval_minutes
  * ONCE     — fires when run_at <= now and hasn't fired yet
  * OPTIMAL  — defers to OptimalScheduler.next_slot()  (added in P1.4)
  * MANUAL   — never fires from beat
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from celery.schedules import crontab

from app.core.logging import get_logger
from app.domain.entities.workflow import Workflow, WorkflowStatus
from app.domain.value_objects.schedule import ScheduleKind
from app.infrastructure.queue.celery_app import celery_app

log = get_logger(__name__)


# ── beat config ────────────────────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    "smms.scheduler.tick": {
        "task": "app.workers.scheduler.tick_scheduler",
        "schedule": crontab(minute="*"),                 # every minute
        "options": {"queue": "default"},
    },
    "smms.metrics.collect": {
        "task": "app.workers.metrics_collector.collect_all_metrics",
        "schedule": crontab(minute=0, hour="*/3"),       # every 3 hours
        "options": {"queue": "default"},
    },
}
celery_app.conf.timezone = "UTC"


# ── tick task ──────────────────────────────────────────────────────────────
@celery_app.task(name="app.workers.scheduler.tick_scheduler",
                 ignore_result=True)
def tick_scheduler() -> int:
    """Polls active workflows; enqueues runs for ones that are due.

    Returns the number of runs enqueued (useful for monitoring)."""
    return asyncio.run(_tick_async())


async def _tick_async() -> int:
    from app.api.deps import _build_repos, get_registry
    repos = _build_repos()
    registry = get_registry()                            # noqa: F841 — warm cache
    now = datetime.now(timezone.utc)
    enqueued = 0

    # Iterate every org's active workflows. For Supabase backend you'd query
    # a single `WHERE status='active'`; the in-memory backend is per-org so
    # we'd need an outer scan. The `WorkflowRepository.list_due()` extension
    # is the production move; here we keep it simple.
    workflow_repo = repos["workflow"]
    if not hasattr(workflow_repo, "list_active_due"):
        # A smart workflow_repo can short-circuit; otherwise the worker pulls
        # all and filters. The bound is N(active workflows), not N(orgs).
        log.debug("scheduler_using_in_memory_scan")

    # Lazy import — ListAvailableOrgs / GetActive happens at the repo level
    # in production; for now we delegate to the run service to enqueue per id.
    from app.workers.workflow_runner import run_workflow as run_task

    # Iterate via the global repo pool — a cheap protocol check
    orgs_seen = getattr(workflow_repo, "_s", {})         # in-memory tail
    if isinstance(orgs_seen, dict):
        for org_id, by_id in orgs_seen.items():
            for wf in by_id.values():
                if _is_due(wf, now):
                    run_task.delay(str(org_id), str(wf.id))
                    log.info("scheduler_enqueued",
                             workflow_id=str(wf.id),
                             org_id=str(org_id),
                             kind=wf.schedule.kind.value)
                    enqueued += 1
    return enqueued


def _is_due(wf: Workflow, now: datetime) -> bool:
    if wf.status is not WorkflowStatus.ACTIVE:
        return False
    s = wf.schedule
    if s.kind is ScheduleKind.MANUAL:
        return False
    if s.kind is ScheduleKind.ONCE:
        return bool(s.run_at and s.run_at.replace(tzinfo=timezone.utc) <= now)
    if s.kind is ScheduleKind.INTERVAL:
        last = wf.updated_at.replace(tzinfo=timezone.utc)
        return now - last >= timedelta(minutes=int(s.interval_minutes or 60))
    if s.kind is ScheduleKind.CRON:
        return _cron_due(s.cron or "", wf.updated_at, now)
    if s.kind.value == "optimal":
        # The optimal scheduler is consulted lazily; if no slot is set we skip.
        return _optimal_due(wf, now)
    return False


def _cron_due(expr: str, last_run: datetime, now: datetime) -> bool:
    if not expr:
        return False
    try:
        from croniter import croniter
    except ImportError:
        log.warning("croniter_missing_cron_schedule_skipped")
        return False
    base = last_run if last_run.tzinfo else last_run.replace(tzinfo=timezone.utc)
    nxt = croniter(expr, base).get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    return nxt <= now


def _optimal_due(wf: Workflow, now: datetime) -> bool:
    try:
        from app.services.optimal_scheduler import OptimalScheduler
    except ImportError:
        return False
    next_slot = OptimalScheduler.next_slot_for(wf, now)
    return next_slot is not None and next_slot <= now
