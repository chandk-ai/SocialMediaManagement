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
    """Pull every ACTIVE workflow across all orgs (single SQL query on
    Supabase; one in-memory scan in dev) and enqueue the ones that are due.

    Why a single cross-tenant scan rather than per-org iteration:
      * Beat runs once a minute on a single dyno, so RLS isn't useful here
        (no user context).
      * Filtering ``WHERE status='active'`` at the DB level keeps the worker
        from materialising paused workflows that we'd just discard anyway.
      * This was previously broken: the old version probed
        ``workflow_repo._s``, which only exists on the in-memory repo.
        Supabase deployments silently returned 0, so cron / interval
        schedules never fired in production.
    """
    from app.api.deps import _build_repos, get_registry
    from app.workers.workflow_runner import run_workflow as run_task

    repos = _build_repos()
    get_registry()                                       # warm the cache
    workflow_repo = repos["workflow"]

    # Every implementation registered in repositories/ports.py now defines
    # list_active_all_orgs(). Older repos still get a graceful no-op.
    list_active = getattr(workflow_repo, "list_active_all_orgs", None)
    if list_active is None:
        log.warning("scheduler_repo_missing_list_active_all_orgs")
        return 0

    now = datetime.now(timezone.utc)
    enqueued = 0
    pairs = await list_active()
    for org_id, wf in pairs:
        if _is_due(wf, now):
            run_task.delay(str(org_id), str(wf.id))
            log.info("scheduler_enqueued",
                     workflow_id=str(wf.id),
                     org_id=str(org_id),
                     kind=wf.schedule.kind.value)
            enqueued += 1

            # ONCE schedules need a "disarm" step or they'll fire on
            # every subsequent tick (run_at <= now stays true forever).
            # We disarm by clearing run_at — the workflow stays active,
            # just with no more scheduled runs until the user reschedules
            # via the UI. INTERVAL and CRON are self-spacing through
            # their own time arithmetic so they don't need this guard.
            if wf.schedule.kind is ScheduleKind.ONCE:
                try:
                    await _disarm_once_schedule(workflow_repo, org_id, wf)
                except Exception as exc:                              # noqa: BLE001
                    # Disarm failure is non-fatal — next tick would
                    # double-fire, which is bad but recoverable, vs.
                    # raising and breaking the whole tick.
                    log.warning("scheduler_disarm_failed",
                                workflow_id=str(wf.id), error=str(exc))
    log.info("scheduler_tick_done",
             active_workflows=len(pairs), enqueued=enqueued)
    return enqueued


async def _disarm_once_schedule(repo, org_id, wf) -> None:
    """A ``Schedule(kind=ONCE)`` requires ``run_at`` (per Schedule's
    own validator), so we can't simply null the run_at field — the
    value object would refuse to construct. We instead downgrade the
    kind to MANUAL once consumed; the user can rearm via the UI by
    picking a new date, which re-creates a fresh ONCE schedule.

    Why downgrade rather than mark-consumed with a flag: the Schedule
    value object is intentionally minimal — adding a 'consumed_at'
    field would propagate through every persistence layer + UI form.
    MANUAL is the natural "no automatic runs" state and already exists.
    """
    from app.domain.value_objects.schedule import Schedule, ScheduleKind

    wf.schedule = Schedule(
        kind=ScheduleKind.MANUAL,
        cron=None,
        interval_minutes=None,
        run_at=None,
        timezone=wf.schedule.timezone,
    )
    await repo.update(wf)


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
    if s.kind.value == "adaptive":
        return _adaptive_due(wf, now)
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


def _adaptive_due(wf: Workflow, now: datetime) -> bool:
    """ADAPTIVE schedule consultation. Looks at the workflow's own
    published-post history to decide whether to fire."""
    try:
        import asyncio
        from app.api.deps import _build_repos
        from app.services.adaptive_scheduler import is_due
    except ImportError:
        return False
    try:
        repos = _build_repos()
        post_repo = repos["post"]
        # Sync wrapper — the Beat task is sync; we only need a small async
        # query. asyncio.run is fine here because each tick is independent.
        async def _gather() -> bool:
            posts = await post_repo.list(wf.org_id)
            wf_posts = [p for p in posts if p.workflow_id == wf.id]
            due, decision = is_due(wf, wf_posts, now)
            log.info(
                "adaptive_schedule_decision",
                workflow_id=str(wf.id),
                interval_min=decision.interval_minutes,
                reason=decision.reason,
                recent_eng=decision.recent_engagement,
                prior_eng=decision.prior_engagement,
                due=due,
            )
            return due
        return asyncio.run(_gather())
    except Exception as exc:                                            # noqa: BLE001
        log.warning("adaptive_schedule_failed_falling_back",
                    workflow_id=str(wf.id), error=str(exc))
        return False
