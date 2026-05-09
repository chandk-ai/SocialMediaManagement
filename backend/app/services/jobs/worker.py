"""Job worker loop.

Run as a sidecar process (``python -m app.services.jobs.worker``) or
as an in-process background task in dev mode. Spawns N coroutines
that poll the queue and dispatch jobs to registered handlers.

Knobs (env-driven; see Settings.workers):
    SMMS_WORKERS_CONCURRENCY  — number of in-process consumers (default 4)
    SMMS_WORKERS_POLL_MS      — polling interval when queue is empty
    SMMS_WORKERS_KINDS        — comma-separated whitelist (default: all)
    SMMS_WORKERS_RECOVERY_S   — orphan-sweep cadence (seconds)
    SMMS_WORKERS_STALE_S      — running-too-long threshold for sweep

The loop is graceful-shutdown aware: SIGTERM marks ``_running=False`` and
in-flight tasks finish their current job before exit. New claims stop.

Telemetry: every dispatch emits a structlog line (job.dispatched,
job.completed, job.failed). Pillar 2 will plug in OTel spans + metrics.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from typing import Any

from app.core.alerts import fire_alert
from app.core.logging import get_logger
from app.core.metrics import M
from app.core.tracing import trace_span
from app.services.jobs.handlers import REGISTRY, HandlerContext
from app.services.jobs.queue import (
    JobQueue, JobStatus, PermanentError, default_worker_id,
)

log = get_logger(__name__)


class JobWorkerPool:
    def __init__(
        self,
        queue: JobQueue,
        services: dict[str, Any] | None = None,
        *,
        concurrency: int = 4,
        kinds: list[str] | None = None,
        poll_ms: int = 500,
        recovery_seconds: float = 60.0,
        stale_seconds: float = 600.0,
        worker_id: str | None = None,
    ) -> None:
        self.queue = queue
        self.services = services or {}
        self.concurrency = max(1, int(concurrency))
        self.kinds = kinds
        self.poll_seconds = max(0.05, poll_ms / 1000.0)
        self.recovery_seconds = recovery_seconds
        self.stale_seconds = stale_seconds
        self.worker_id = worker_id or default_worker_id()
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._sweeper: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        log.info("worker_pool_starting", worker_id=self.worker_id,
                 concurrency=self.concurrency, kinds=self.kinds or "ALL")
        for i in range(self.concurrency):
            self._tasks.append(asyncio.create_task(
                self._consume_loop(i), name=f"job-consumer-{i}"
            ))
        self._sweeper = asyncio.create_task(self._sweep_loop(),
                                            name="job-orphan-sweeper")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        log.info("worker_pool_stopping", worker_id=self.worker_id)
        for t in self._tasks:
            t.cancel()
        if self._sweeper:
            self._sweeper.cancel()
        for t in [*self._tasks, self._sweeper]:
            if t:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
        self._tasks.clear()
        self._sweeper = None

    async def _consume_loop(self, idx: int) -> None:
        wid = f"{self.worker_id}#{idx}"
        while self._running:
            try:
                jobs = await self.queue.claim(
                    wid, kinds=self.kinds, batch=1,
                )
            except Exception as exc:                                 # noqa: BLE001
                log.warning("worker_claim_failed", worker=wid, error=str(exc))
                await asyncio.sleep(self.poll_seconds * 4)
                continue
            if not jobs:
                await asyncio.sleep(self.poll_seconds)
                continue
            for j in jobs:
                await self._dispatch(j)

    async def _dispatch(self, job) -> None:
        handler = REGISTRY.get(job.kind)
        if handler is None:
            log.error("job_unknown_kind", kind=job.kind, job_id=job.id)
            await self.queue.fail(job.id,
                                  error=f"no handler registered for {job.kind}",
                                  permanent=True)
            return

        log.info("job_dispatched", kind=job.kind, job_id=job.id,
                 attempt=job.attempt, run_id=job.run_id, org_id=job.org_id)
        ctx = HandlerContext(job=job, queue=self.queue,
                             services=self.services)
        t0 = time.time()
        with trace_span("job.dispatch",
                        **{"job.kind": job.kind, "job.id": job.id,
                           "job.attempt": job.attempt,
                           "run.id": job.run_id, "org.id": job.org_id}) as span, \
             M.job_duration.labels(kind=job.kind).time():
            try:
                result = await handler(ctx)
                await self.queue.complete(job.id, result=result or {})
                M.jobs_processed.labels(
                    kind=job.kind, status=JobStatus.SUCCEEDED.value).inc()
                log.info("job_completed", kind=job.kind, job_id=job.id,
                         duration_ms=int((time.time() - t0) * 1000))
            except PermanentError as exc:
                span.record_exception(exc)
                M.jobs_processed.labels(
                    kind=job.kind, status=JobStatus.DEAD.value).inc()
                log.warning("job_failed_permanent", kind=job.kind,
                            job_id=job.id, error=str(exc))
                await self.queue.fail(job.id, error=str(exc), permanent=True)
                await fire_alert(
                    title=f"Job DLQ: {job.kind}",
                    body=f"Permanent failure on job {job.id}: {exc}",
                    severity="error",
                    alert_key=f"dlq:{job.kind}:{job.id}",
                    job_id=job.id, kind=job.kind, org_id=job.org_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:                                 # noqa: BLE001
                span.record_exception(exc)
                M.jobs_processed.labels(
                    kind=job.kind, status=JobStatus.FAILED.value).inc()
                log.warning("job_failed_transient", kind=job.kind,
                            job_id=job.id, attempt=job.attempt,
                            error=str(exc))
                rec = await self.queue.fail(job.id, error=str(exc))
                # If we just exhausted retries, page on-call.
                if rec.status == JobStatus.DEAD:
                    await fire_alert(
                        title=f"Job exhausted retries: {job.kind}",
                        body=f"Job {job.id} reached DLQ after {job.attempt} attempts: {exc}",
                        severity="error",
                        alert_key=f"dlq-retry:{job.id}",
                        job_id=job.id, kind=job.kind, org_id=job.org_id,
                    )

    async def _sweep_loop(self) -> None:
        while self._running:
            try:
                n = await self.queue.sweep_orphans(self.stale_seconds)
                if n:
                    log.info("worker_sweep_recovered", count=n)
            except Exception as exc:                                 # noqa: BLE001
                log.warning("worker_sweep_failed", error=str(exc))
            await asyncio.sleep(self.recovery_seconds)


async def _amain() -> None:
    """Standalone entrypoint. Loads handlers, builds a queue from
    settings, runs until SIGTERM."""
    # Importing handlers registers them via decorator side-effect.
    from app.services.jobs import handlers_workflow  # noqa: F401
    from app.services.jobs import handlers_engagement  # noqa: F401

    queue = await _build_queue_from_env()
    services = await _build_services_from_env()

    pool = JobWorkerPool(
        queue, services,
        concurrency=int(os.environ.get("SMMS_WORKERS_CONCURRENCY", "4")),
        kinds=_parse_kinds(os.environ.get("SMMS_WORKERS_KINDS", "")),
        poll_ms=int(os.environ.get("SMMS_WORKERS_POLL_MS", "500")),
        recovery_seconds=float(
            os.environ.get("SMMS_WORKERS_RECOVERY_S", "60")),
        stale_seconds=float(
            os.environ.get("SMMS_WORKERS_STALE_S", "600")),
    )

    stop = asyncio.Event()

    def _handle_signal():
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    await pool.start()
    log.info("worker_main_ready")
    await stop.wait()
    await pool.stop()
    log.info("worker_main_done")


def _parse_kinds(val: str) -> list[str] | None:
    val = (val or "").strip()
    if not val:
        return None
    return [k.strip() for k in val.split(",") if k.strip()]


async def _build_queue_from_env() -> JobQueue:
    from app.core.config import get_settings
    settings = get_settings()
    if settings.resolved_persistence_backend() == "supabase":
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.services.jobs.queue import PostgresJobQueue
        engine = create_async_engine(
            settings.db_url(), pool_pre_ping=True,
            connect_args=settings.db_connect_args(),
        )
        sm = async_sessionmaker(engine, expire_on_commit=False)
        return PostgresJobQueue(sm)
    from app.services.jobs.queue import InMemoryJobQueue
    return InMemoryJobQueue()


async def _build_services_from_env() -> dict[str, Any]:
    """Construct the handles a workflow handler would need. The worker
    is best thought of as a headless API process, so we wire the same
    DI paths."""
    from app.api import deps
    return {
        "workflow_service": deps.get_workflow_service(),
        "post_service": deps.get_post_service(),
        "platform_service": deps.get_platform_service(),
        "source_service": deps.get_source_service(),
        "audit_log": deps.get_audit_log_service(),
        "engagement_service": deps.get_engagement_service()
            if hasattr(deps, "get_engagement_service") else None,
    }


if __name__ == "__main__":
    asyncio.run(_amain())
