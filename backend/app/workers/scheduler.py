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
        if not _is_due(wf, now):
            continue

        run_task.delay(str(org_id), str(wf.id))
        log.info("scheduler_enqueued",
                 workflow_id=str(wf.id),
                 org_id=str(org_id),
                 kind=wf.schedule.kind.value)
        enqueued += 1

        # Two post-fire bookkeeping concerns, in priority order:
        #
        # 1. **Stamp last_fired_at** — every kind needs this. Without
        #    it _is_due() can't tell "first time" from "I've already
        #    fired this minute" and the workflow would re-fire on
        #    every subsequent tick until updated_at advanced for some
        #    other reason. Applies to INTERVAL, CRON, OPTIMAL, ADAPTIVE.
        #
        # 2. **Disarm ONCE** — separate concern. A ONCE schedule has
        #    semantically completed once it fires. We downgrade kind
        #    to MANUAL so the UI shows the right state and the user
        #    can rearm. (Just stamping last_fired_at is not enough
        #    for ONCE because s.run_at is still ≤ now.)
        try:
            await _record_fired(workflow_repo, wf, now)
        except Exception as exc:                                      # noqa: BLE001
            log.warning("scheduler_record_fired_failed",
                        workflow_id=str(wf.id), error=str(exc))

    log.info("scheduler_tick_done",
             active_workflows=len(pairs), enqueued=enqueued)
    return enqueued


async def _record_fired(repo, wf, fired_at: datetime) -> None:
    """Stamp ``last_fired_at`` so the next tick knows when this
    workflow last fired, and for ONCE schedules also downgrade kind
    to MANUAL so the UI shows the right state.

    Why this is one function: both writes happen in the same UPDATE
    statement (one repo.update call), keeping the bookkeeping atomic.
    A worker crash between the two would otherwise leave a ONCE
    schedule still armed despite last_fired_at being set, then double-
    fire on the next tick.
    """
    from app.domain.value_objects.schedule import Schedule, ScheduleKind

    wf.last_fired_at = fired_at
    if wf.schedule.kind is ScheduleKind.ONCE:
        wf.schedule = Schedule(
            kind=ScheduleKind.MANUAL,
            cron=None,
            interval_minutes=None,
            run_at=None,
            timezone=wf.schedule.timezone,
        )
    await repo.update(wf)


def _last_fire_anchor(wf: Workflow) -> datetime:
    """The reference timestamp for "has enough time passed since the
    workflow last fired?". Prefers ``last_fired_at`` (real fire time);
    falls back to ``updated_at`` for workflows that have never fired
    so the first eligibility check still happens at a sane moment.

    Always returned tz-aware (UTC). Schedule comparisons elsewhere
    in this module assume tz-aware datetimes — a naive comparison
    would raise.
    """
    candidate = wf.last_fired_at or wf.updated_at
    return (
        candidate.replace(tzinfo=timezone.utc)
        if candidate.tzinfo is None else candidate
    )


def _is_due(wf: Workflow, now: datetime) -> bool:
    if wf.status is not WorkflowStatus.ACTIVE:
        return False
    s = wf.schedule
    if s.kind is ScheduleKind.MANUAL:
        return False

    anchor = _last_fire_anchor(wf)

    if s.kind is ScheduleKind.ONCE:
        # ONCE: fire when run_at has passed AND we haven't fired yet
        # (or last_fired_at predates run_at — a fresh re-arm).
        if not s.run_at:
            return False
        run_at = (
            s.run_at.replace(tzinfo=timezone.utc)
            if s.run_at.tzinfo is None else s.run_at
        )
        if run_at > now:
            return False
        # If last_fired_at is set AND it's at-or-after the scheduled
        # run_at, this ONCE has already been consumed. The scheduler
        # also downgrades kind=MANUAL on consumption (see _record_fired)
        # so this branch is belt-and-braces for the deploy window
        # where last_fired_at is set but the kind transition hasn't
        # propagated yet (e.g. an old worker still reading the row).
        return not (wf.last_fired_at and wf.last_fired_at >= run_at)

    if s.kind is ScheduleKind.INTERVAL:
        return now - anchor >= timedelta(minutes=int(s.interval_minutes or 60))

    if s.kind is ScheduleKind.CRON:
        return _cron_due(s.cron or "", anchor, now)

    if s.kind.value == "optimal":
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
