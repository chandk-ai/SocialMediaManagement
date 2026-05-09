"""JobQueue contract + dual implementation.

The contract is small on purpose:

    enqueue(kind, org_id, payload, *, run_id, priority, scheduled_for,
            idempotency_key, max_attempts) -> JobRecord
    claim(worker_id, *, kinds, batch) -> list[JobRecord]
    complete(job_id, *, result) -> None
    fail(job_id, *, error, retry_in) -> JobRecord  # bumps attempt, may DLQ
    cancel(job_id) -> None
    get(job_id) -> JobRecord | None
    list_for_org(org_id, *, status, kind, limit) -> list[JobRecord]
    sweep_orphans(stale_seconds) -> int  # recovery hook

Two backends share this surface:

* **Postgres** uses ``smms.jobs`` with ``FOR UPDATE SKIP LOCKED`` so
  multiple worker processes can safely claim concurrently.
* **InMemory** uses an asyncio.Lock + a list. Single-process only, but
  same semantics — tests don't have to know which they're running on.

Backoff schedule for ``fail()``: exponential with jitter, capped at
the worker config's max delay. The handler can short-circuit by
raising ``PermanentError`` to skip retries (e.g., the LLM said the
content violated policy — retrying won't fix that).
"""
from __future__ import annotations

import asyncio
import json
import random
import socket
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.core.metrics import M

log = get_logger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"            # transient — may retry
    DEAD = "dead"                # exhausted retries
    CANCELLED = "cancelled"


class JobNotFound(Exception):
    pass


class DuplicateIdempotencyKey(Exception):
    """Raised when enqueue() is called with an idempotency_key that
    already exists for the org. Caller should treat as success and
    return the existing job_id."""
    def __init__(self, existing_job_id: str):
        super().__init__(f"duplicate idempotency key — existing job {existing_job_id}")
        self.existing_job_id = existing_job_id


class PermanentError(Exception):
    """Raised by handlers to skip retries — e.g. the LLM rejected a
    prompt for safety reasons, no amount of re-running will help."""


@dataclass(slots=True)
class JobRecord:
    id: str
    org_id: str
    kind: str
    payload: dict[str, Any]
    status: JobStatus = JobStatus.QUEUED
    attempt: int = 0
    max_attempts: int = 5
    priority: int = 100
    scheduled_for: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str | None = None
    claimed_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── interface ──────────────────────────────────────────────────────────
class JobQueue(ABC):
    @abstractmethod
    async def enqueue(
        self, kind: str, org_id: str | UUID, payload: dict[str, Any], *,
        run_id: str | UUID | None = None,
        priority: int = 100,
        scheduled_for: datetime | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 5,
    ) -> JobRecord: ...

    @abstractmethod
    async def claim(
        self, worker_id: str, *, kinds: list[str] | None = None, batch: int = 1,
    ) -> list[JobRecord]: ...

    @abstractmethod
    async def complete(
        self, job_id: str | UUID, *, result: dict[str, Any] | None = None,
    ) -> None: ...

    @abstractmethod
    async def fail(
        self, job_id: str | UUID, *, error: str, retry_in: float | None = None,
        permanent: bool = False,
    ) -> JobRecord: ...

    @abstractmethod
    async def cancel(self, job_id: str | UUID) -> None: ...

    @abstractmethod
    async def get(self, job_id: str | UUID) -> JobRecord | None: ...

    @abstractmethod
    async def list_for_org(
        self, org_id: str | UUID, *, status: str | None = None,
        kind: str | None = None, run_id: str | UUID | None = None,
        limit: int = 100,
    ) -> list[JobRecord]: ...

    @abstractmethod
    async def sweep_orphans(self, stale_seconds: float = 600.0) -> int: ...


# ── memory ─────────────────────────────────────────────────────────────
class InMemoryJobQueue(JobQueue):
    """Single-process queue. Mirrors the Postgres semantics so tests
    don't branch on backend. Locks all mutations so concurrent claim
    calls give consistent results within one process."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self, kind, org_id, payload, *,
        run_id=None, priority=100, scheduled_for=None,
        idempotency_key=None, max_attempts=5,
    ) -> JobRecord:
        async with self._lock:
            if idempotency_key:
                for j in self._jobs.values():
                    if (j.org_id == str(org_id)
                            and j.idempotency_key == idempotency_key):
                        raise DuplicateIdempotencyKey(j.id)
            jid = str(uuid.uuid4())
            rec = JobRecord(
                id=jid, org_id=str(org_id), kind=kind,
                payload=dict(payload or {}),
                run_id=str(run_id) if run_id else None,
                priority=int(priority),
                scheduled_for=scheduled_for or datetime.now(timezone.utc),
                idempotency_key=idempotency_key,
                max_attempts=int(max_attempts),
            )
            self._jobs[jid] = rec
            try:
                M.jobs_enqueued.labels(kind=kind).inc()
            except Exception:                                        # noqa: BLE001
                pass
            return rec

    async def claim(self, worker_id, *, kinds=None, batch=1):
        async with self._lock:
            now = datetime.now(timezone.utc)
            ready = [
                j for j in self._jobs.values()
                if j.status == JobStatus.QUEUED
                and j.scheduled_for <= now
                and (kinds is None or j.kind in kinds)
            ]
            ready.sort(key=lambda j: (j.priority, j.scheduled_for, j.id))
            picked = ready[:batch]
            for j in picked:
                j.status = JobStatus.RUNNING
                j.attempt += 1
                j.claimed_by = worker_id
                j.started_at = now
                j.updated_at = now
            return picked

    async def complete(self, job_id, *, result=None):
        async with self._lock:
            j = self._jobs.get(str(job_id))
            if not j:
                raise JobNotFound(str(job_id))
            j.status = JobStatus.SUCCEEDED
            j.result = result or {}
            j.finished_at = datetime.now(timezone.utc)
            j.updated_at = j.finished_at

    async def fail(self, job_id, *, error, retry_in=None, permanent=False):
        async with self._lock:
            j = self._jobs.get(str(job_id))
            if not j:
                raise JobNotFound(str(job_id))
            j.error = error[:4000]
            j.finished_at = datetime.now(timezone.utc)
            j.updated_at = j.finished_at
            if permanent or j.attempt >= j.max_attempts:
                j.status = JobStatus.DEAD
            else:
                j.status = JobStatus.QUEUED
                delay = retry_in if retry_in is not None \
                    else _backoff(j.attempt)
                j.scheduled_for = datetime.now(timezone.utc) \
                    + timedelta(seconds=delay)
            return j

    async def cancel(self, job_id):
        async with self._lock:
            j = self._jobs.get(str(job_id))
            if not j:
                raise JobNotFound(str(job_id))
            j.status = JobStatus.CANCELLED
            j.finished_at = datetime.now(timezone.utc)

    async def get(self, job_id):
        return self._jobs.get(str(job_id))

    async def list_for_org(self, org_id, *, status=None, kind=None,
                            run_id=None, limit=100):
        rows = [
            j for j in self._jobs.values()
            if j.org_id == str(org_id)
            and (status is None or j.status == status)
            and (kind is None or j.kind == kind)
            and (run_id is None or j.run_id == str(run_id))
        ]
        rows.sort(key=lambda j: j.created_at, reverse=True)
        return rows[: int(limit)]

    async def sweep_orphans(self, stale_seconds=600.0):
        async with self._lock:
            now = datetime.now(timezone.utc)
            n = 0
            for j in self._jobs.values():
                if j.status == JobStatus.RUNNING and j.started_at \
                        and (now - j.started_at).total_seconds() > stale_seconds:
                    j.status = JobStatus.QUEUED
                    j.scheduled_for = now + timedelta(seconds=_backoff(j.attempt))
                    j.error = "(orphaned — worker did not finish in time)"
                    j.claimed_by = None
                    j.started_at = None
                    n += 1
            return n


# ── postgres ───────────────────────────────────────────────────────────
class PostgresJobQueue(JobQueue):
    """SKIP LOCKED claim implementation. The session_maker is the
    same async_sessionmaker the rest of the services use. Caller
    is responsible for connection lifetime."""

    def __init__(self, session_maker) -> None:
        self._sm = session_maker

    async def enqueue(
        self, kind, org_id, payload, *,
        run_id=None, priority=100, scheduled_for=None,
        idempotency_key=None, max_attempts=5,
    ) -> JobRecord:
        from sqlalchemy import text
        sched = scheduled_for or datetime.now(timezone.utc)
        async with self._sm() as s:
            # Idempotency: peek first.
            if idempotency_key:
                row = await s.execute(text("""
                    SELECT id::text FROM smms.jobs
                    WHERE org_id = :org AND idempotency_key = :ik
                    LIMIT 1
                """), {"org": str(org_id), "ik": idempotency_key})
                existing = row.first()
                if existing:
                    raise DuplicateIdempotencyKey(existing[0])

            row = await s.execute(text("""
                INSERT INTO smms.jobs
                  (org_id, kind, payload, run_id, priority,
                   scheduled_for, idempotency_key, max_attempts)
                VALUES
                  (:org, :kind, CAST(:payload AS JSONB), :run_id, :priority,
                   :sched, :ik, :ma)
                RETURNING id::text
            """), {
                "org": str(org_id), "kind": kind,
                "payload": json.dumps(payload or {}),
                "run_id": str(run_id) if run_id else None,
                "priority": int(priority),
                "sched": sched, "ik": idempotency_key, "ma": int(max_attempts),
            })
            jid = row.scalar_one()
            await s.commit()
            try:
                M.jobs_enqueued.labels(kind=kind).inc()
            except Exception:                                        # noqa: BLE001
                pass
            return JobRecord(
                id=jid, org_id=str(org_id), kind=kind,
                payload=dict(payload or {}),
                run_id=str(run_id) if run_id else None,
                priority=int(priority), scheduled_for=sched,
                idempotency_key=idempotency_key,
                max_attempts=int(max_attempts),
            )

    async def claim(self, worker_id, *, kinds=None, batch=1):
        from sqlalchemy import text
        async with self._sm() as s:
            params: dict[str, Any] = {"worker": worker_id, "limit": int(batch)}
            kind_filter = ""
            if kinds:
                kind_filter = "AND kind = ANY(:kinds)"
                params["kinds"] = list(kinds)
            row = await s.execute(text(f"""
                WITH picked AS (
                  SELECT id FROM smms.jobs
                  WHERE status = 'queued'
                    AND scheduled_for <= now()
                    {kind_filter}
                  ORDER BY priority ASC, scheduled_for ASC, id
                  FOR UPDATE SKIP LOCKED
                  LIMIT :limit
                )
                UPDATE smms.jobs j
                   SET status='running',
                       attempt = j.attempt + 1,
                       claimed_by = :worker,
                       started_at = now()
                  FROM picked
                 WHERE j.id = picked.id
                 RETURNING j.id::text, j.org_id::text, j.kind, j.payload,
                           j.status, j.attempt, j.max_attempts, j.priority,
                           j.scheduled_for, j.run_id::text, j.claimed_by,
                           j.started_at, j.finished_at, j.error, j.result,
                           j.idempotency_key, j.created_at, j.updated_at
            """), params)
            recs = [_row_to_record(r) for r in row.fetchall()]
            await s.commit()
            return recs

    async def complete(self, job_id, *, result=None):
        from sqlalchemy import text
        async with self._sm() as s:
            r = await s.execute(text("""
                UPDATE smms.jobs
                   SET status='succeeded', result=CAST(:res AS JSONB),
                       finished_at=now(), error=NULL
                 WHERE id=:jid
                 RETURNING id
            """), {"jid": str(job_id),
                   "res": json.dumps(result or {})})
            if r.first() is None:
                raise JobNotFound(str(job_id))
            await s.commit()

    async def fail(self, job_id, *, error, retry_in=None, permanent=False):
        from sqlalchemy import text
        async with self._sm() as s:
            cur = await s.execute(text("""
                SELECT attempt, max_attempts FROM smms.jobs WHERE id=:jid
            """), {"jid": str(job_id)})
            row = cur.first()
            if not row:
                raise JobNotFound(str(job_id))
            attempt, max_attempts = row[0], row[1]
            will_retry = (not permanent) and (attempt < max_attempts)
            if will_retry:
                delay = retry_in if retry_in is not None else _backoff(attempt)
                next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                await s.execute(text("""
                    UPDATE smms.jobs
                       SET status='queued', error=:err, finished_at=now(),
                           scheduled_for=:next, claimed_by=NULL,
                           started_at=NULL
                     WHERE id=:jid
                """), {"jid": str(job_id), "err": error[:4000],
                       "next": next_at})
            else:
                await s.execute(text("""
                    UPDATE smms.jobs
                       SET status='dead', error=:err, finished_at=now()
                     WHERE id=:jid
                """), {"jid": str(job_id), "err": error[:4000]})
            await s.commit()
            j = await self.get(job_id)
            assert j is not None
            return j

    async def cancel(self, job_id):
        from sqlalchemy import text
        async with self._sm() as s:
            r = await s.execute(text("""
                UPDATE smms.jobs SET status='cancelled', finished_at=now()
                 WHERE id=:jid AND status IN ('queued','running')
                 RETURNING id
            """), {"jid": str(job_id)})
            if r.first() is None:
                raise JobNotFound(str(job_id))
            await s.commit()

    async def get(self, job_id):
        from sqlalchemy import text
        async with self._sm() as s:
            r = await s.execute(text("""
                SELECT id::text, org_id::text, kind, payload, status,
                       attempt, max_attempts, priority, scheduled_for,
                       run_id::text, claimed_by, started_at, finished_at,
                       error, result, idempotency_key, created_at, updated_at
                  FROM smms.jobs WHERE id=:jid
            """), {"jid": str(job_id)})
            row = r.first()
            return _row_to_record(row) if row else None

    async def list_for_org(self, org_id, *, status=None, kind=None,
                            run_id=None, limit=100):
        from sqlalchemy import text
        clauses = ["org_id=:org"]
        params: dict[str, Any] = {"org": str(org_id), "limit": int(limit)}
        if status:
            clauses.append("status=:status")
            params["status"] = status
        if kind:
            clauses.append("kind=:kind")
            params["kind"] = kind
        if run_id:
            clauses.append("run_id=:rid")
            params["rid"] = str(run_id)
        async with self._sm() as s:
            r = await s.execute(text(f"""
                SELECT id::text, org_id::text, kind, payload, status,
                       attempt, max_attempts, priority, scheduled_for,
                       run_id::text, claimed_by, started_at, finished_at,
                       error, result, idempotency_key, created_at, updated_at
                  FROM smms.jobs
                 WHERE {' AND '.join(clauses)}
                 ORDER BY created_at DESC LIMIT :limit
            """), params)
            return [_row_to_record(row) for row in r.fetchall()]

    async def sweep_orphans(self, stale_seconds=600.0):
        from sqlalchemy import text
        async with self._sm() as s:
            r = await s.execute(text("""
                UPDATE smms.jobs
                   SET status='queued',
                       scheduled_for = now() + (interval '1 second' * :delay),
                       error = COALESCE(error,'') || '; orphan-recovered',
                       claimed_by = NULL,
                       started_at = NULL
                 WHERE status='running'
                   AND started_at < now() - (interval '1 second' * :stale)
                 RETURNING id
            """), {"stale": float(stale_seconds), "delay": float(_backoff(1))})
            n = len(r.fetchall())
            await s.commit()
            return n


def _row_to_record(row) -> JobRecord:
    payload = row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}")
    result = row[14]
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:                                          # noqa: BLE001
            pass
    return JobRecord(
        id=row[0], org_id=row[1], kind=row[2], payload=payload,
        status=JobStatus(row[4]), attempt=int(row[5]),
        max_attempts=int(row[6]), priority=int(row[7]),
        scheduled_for=row[8], run_id=row[9], claimed_by=row[10],
        started_at=row[11], finished_at=row[12], error=row[13],
        result=result, idempotency_key=row[15],
        created_at=row[16], updated_at=row[17],
    )


def _backoff(attempt: int) -> float:
    """Exponential backoff with full jitter. Capped at 5 minutes."""
    base = min(300.0, (2 ** max(0, attempt - 1)))
    return random.uniform(base * 0.5, base)


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
