"""System-job scheduler — periodic enqueues for cross-cutting work.

Runs as a goroutine inside the worker pool (one instance, leader-elected
via a Postgres advisory lock so multi-replica worker pools don't double-
enqueue). Periodically pushes:

    * engagement.aggregate         every 10 min  per active org
    * knowledge.auto_ingest        every 1 hour  per active org
    * jobs.cleanup                 every 6 hours globally

Why advisory lock instead of a separate beat process: the Celery beat
container is already in the topology for legacy reasons; we don't want
to add another singleton. Postgres ``pg_try_advisory_lock(KEY)`` lets
*any* worker take the leadership slot; if it dies, the lock releases
and another worker grabs it on the next tick.

When the durable persistence backend is in-memory (dev / tests), the
advisory lock is skipped — there's only one process anyway.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


# Tick cadences (seconds) — defaults; override via env in worker.py.
ENGAGEMENT_AGGREGATE_S = 10 * 60       # 10 minutes
KB_AUTO_INGEST_S       = 60 * 60       # 1 hour
JOBS_CLEANUP_S         = 6 * 60 * 60   # 6 hours

# Postgres advisory lock key — arbitrary 64-bit int unique to this scheduler.
ADVISORY_LOCK_KEY = 7_725_119_911_001  # "smms-system-scheduler"


class SystemScheduler:
    def __init__(
        self, queue, *, session_maker=None,
        engagement_period: float = ENGAGEMENT_AGGREGATE_S,
        kb_period: float = KB_AUTO_INGEST_S,
        cleanup_period: float = JOBS_CLEANUP_S,
        tick_seconds: float = 60.0,
    ) -> None:
        self.queue = queue
        self.sm = session_maker
        self.tick = float(tick_seconds)
        self.periods = {
            "engagement.aggregate": float(engagement_period),
            "knowledge.auto_ingest": float(kb_period),
            "jobs.cleanup": float(cleanup_period),
        }
        self._last_run: dict[str, float] = {k: 0.0 for k in self.periods}
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="system-scheduler")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):              # noqa: BLE001
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                if await self._is_leader():
                    await self._run_due_kinds()
            except Exception as exc:                                 # noqa: BLE001
                log.warning("system_scheduler_tick_failed", error=str(exc))
            await asyncio.sleep(self.tick)

    async def _is_leader(self) -> bool:
        """Take pg_try_advisory_lock so only one worker schedules at a
        time. Memory backend → always leader."""
        if self.sm is None:
            return True
        try:
            from sqlalchemy import text
            async with self.sm() as s:
                r = await s.execute(text(
                    "SELECT pg_try_advisory_lock(:k)"
                ), {"k": ADVISORY_LOCK_KEY})
                got = bool(r.scalar())
                return got
        except Exception as exc:                                     # noqa: BLE001
            log.info("system_scheduler_no_leadership_check",
                     error=str(exc))
            # Fall through — if the lock probe failed, behave as leader
            # (worst case is duplicate enqueues with idempotency keys
            # de-duping them).
            return True

    async def _run_due_kinds(self) -> None:
        now = time.time()
        for kind, period in self.periods.items():
            if now - self._last_run[kind] < period:
                continue
            self._last_run[kind] = now
            try:
                await self._enqueue_kind(kind)
            except Exception as exc:                                 # noqa: BLE001
                log.warning("system_scheduler_enqueue_failed",
                            kind=kind, error=str(exc))

    async def _enqueue_kind(self, kind: str) -> None:
        """Per-kind dispatch — most are per-org, jobs.cleanup is global."""
        if kind == "jobs.cleanup":
            await self.queue.enqueue(
                kind, _SYSTEM_ORG, {},
                idempotency_key=f"sys:cleanup:{int(time.time() // 21600)}",
                priority=300,
            )
            return

        # Per-org dispatch — needs the active-org list.
        org_ids = await self._active_orgs()
        for org_id in org_ids:
            ik = f"sys:{kind}:{org_id}:{int(time.time() // self.periods[kind])}"
            try:
                await self.queue.enqueue(
                    kind, org_id, {}, idempotency_key=ik, priority=300,
                )
            except Exception as exc:                                 # noqa: BLE001
                # Idempotency duplicate is fine; everything else is logged.
                if "duplicate" not in str(exc).lower():
                    log.info("system_scheduler_enqueue_dropped",
                             kind=kind, org=org_id, reason=str(exc))

    async def _active_orgs(self) -> list[str]:
        """Org list — anyone with at least one workflow OR one post in the
        last 7 days. Cheap query, indexed via existing FKs."""
        if self.sm is None:
            return []
        try:
            from sqlalchemy import text
            async with self.sm() as s:
                r = await s.execute(text("""
                    SELECT DISTINCT org_id::text FROM smms.workflows
                    UNION
                    SELECT DISTINCT org_id::text FROM smms.posts
                     WHERE created_at > now() - interval '7 days'
                """))
                return [row[0] for row in r.fetchall() if row[0]]
        except Exception as exc:                                     # noqa: BLE001
            log.info("system_scheduler_active_orgs_failed",
                     error=str(exc))
            return []


# Sentinel used for global-scope jobs (jobs.cleanup) — uses NULL semantics
# everywhere except the queue's NOT NULL org_id column. We pass the ID of
# a designated system org if present, else fall back to the first org.
_SYSTEM_ORG = "00000000-0000-0000-0000-000000000000"


def make_scheduler_from_queue(queue: Any) -> SystemScheduler | None:
    """Helper used by worker.py: returns None for the in-memory queue
    (no point scheduling anything in dev), else a fully wired scheduler."""
    sm = getattr(queue, "_sm", None)
    if sm is None:
        return None
    return SystemScheduler(queue, session_maker=sm)
