"""Per-(org, target) circuit breaker.

Why this exists
───────────────
LinkedIn returning 503 for the next 10 minutes shouldn't translate to
us hammering them with 200 retries. The breaker:

    closed   → normal — every call goes through.
    open     → fail fast for ``cooldown_seconds`` after ``failure_threshold``
               consecutive failures. No outbound call attempted.
    half_open→ on the next probe, allow ONE call through. If it succeeds
               we drop back to closed; if it fails we re-open with a
               fresh cooldown.

State lives in ``smms.circuit_state`` (Postgres) or in-memory. Adapters
call ``await breaker.guard(org_id, kind, target_id, fn)`` and the breaker
either runs ``fn()`` or raises ``CircuitOpen``.

The ``fn`` raising or returning truthy ``error`` is treated as failure;
otherwise success. We update state inside the same call so concurrent
adapters across the worker pool converge on the same state.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from app.core.logging import get_logger

log = get_logger(__name__)


class CircuitOpen(Exception):
    """Raised by ``CircuitBreaker.guard()`` when the breaker is open
    and we're refusing to call out. Caller (typically a publish
    handler) should fail the job with retry — the worker's backoff
    naturally aligns with the breaker cooldown."""
    def __init__(self, kind: str, target: str, retry_at: datetime):
        super().__init__(f"circuit open for {kind}:{target} until {retry_at.isoformat()}")
        self.kind = kind
        self.target = target
        self.retry_at = retry_at


@dataclass(slots=True)
class CircuitState:
    org_id: str
    kind: str
    target: str
    state: str = "closed"  # closed | open | half_open
    consecutive_fail: int = 0
    consecutive_ok: int = 0
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None
    opened_at: datetime | None = None
    next_probe_at: datetime | None = None
    failure_threshold: int = 5
    cooldown_seconds: int = 60


class CircuitBreaker(ABC):
    @abstractmethod
    async def status(self, org_id: str, kind: str, target: str) -> CircuitState: ...

    @abstractmethod
    async def record_success(self, org_id: str, kind: str, target: str) -> None: ...

    @abstractmethod
    async def record_failure(self, org_id: str, kind: str, target: str,
                              *, error: str | None = None) -> None: ...

    async def guard(
        self, org_id: str, kind: str, target: str,
        fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        st = await self.status(org_id, kind, target)
        now = datetime.now(timezone.utc)
        if st.state == "open":
            if st.next_probe_at and st.next_probe_at <= now:
                # Promote to half-open — one probe slot available.
                await self._set_half_open(org_id, kind, target)
            else:
                raise CircuitOpen(kind, target,
                                  st.next_probe_at or now)
        try:
            result = await fn()
        except Exception:                                            # noqa: BLE001
            await self.record_failure(org_id, kind, target)
            raise
        await self.record_success(org_id, kind, target)
        return result

    @abstractmethod
    async def _set_half_open(self, org_id: str, kind: str, target: str) -> None: ...


# ── memory ─────────────────────────────────────────────────────────────
class InMemoryCircuitBreaker(CircuitBreaker):
    def __init__(self) -> None:
        self._state: dict[tuple[str, str, str], CircuitState] = {}
        self._lock = asyncio.Lock()

    async def _get(self, org_id, kind, target) -> CircuitState:
        key = (str(org_id), kind, target)
        st = self._state.get(key)
        if st is None:
            st = CircuitState(org_id=str(org_id), kind=kind, target=target)
            self._state[key] = st
        return st

    async def status(self, org_id, kind, target):
        async with self._lock:
            return CircuitState(**self._snapshot(await self._get(org_id, kind, target)))

    async def record_success(self, org_id, kind, target):
        async with self._lock:
            st = await self._get(org_id, kind, target)
            st.consecutive_fail = 0
            st.consecutive_ok += 1
            st.last_success_at = datetime.now(timezone.utc)
            if st.state in ("open", "half_open"):
                log.info("circuit_recovered", kind=kind, target=target)
            st.state = "closed"
            st.opened_at = None
            st.next_probe_at = None

    async def record_failure(self, org_id, kind, target, *, error=None):
        async with self._lock:
            st = await self._get(org_id, kind, target)
            st.consecutive_ok = 0
            st.consecutive_fail += 1
            st.last_failure_at = datetime.now(timezone.utc)
            if st.consecutive_fail >= st.failure_threshold:
                st.state = "open"
                st.opened_at = datetime.now(timezone.utc)
                st.next_probe_at = st.opened_at + timedelta(
                    seconds=st.cooldown_seconds)
                log.warning("circuit_tripped", kind=kind, target=target,
                            consecutive_fail=st.consecutive_fail)

    async def _set_half_open(self, org_id, kind, target):
        async with self._lock:
            st = await self._get(org_id, kind, target)
            st.state = "half_open"

    def _snapshot(self, st: CircuitState) -> dict:
        return {k: getattr(st, k) for k in CircuitState.__slots__}


# ── postgres ───────────────────────────────────────────────────────────
class PostgresCircuitBreaker(CircuitBreaker):
    def __init__(self, session_maker) -> None:
        self._sm = session_maker

    async def status(self, org_id, kind, target):
        from sqlalchemy import text
        async with self._sm() as s:
            r = await s.execute(text("""
                SELECT org_id::text, target_kind, target_id, state,
                       consecutive_fail, consecutive_ok,
                       last_failure_at, last_success_at, opened_at,
                       next_probe_at, failure_threshold, cooldown_seconds
                  FROM smms.circuit_state
                 WHERE org_id=:org AND target_kind=:k AND target_id=:t
            """), {"org": str(org_id), "k": kind, "t": target})
            row = r.first()
            if not row:
                return CircuitState(org_id=str(org_id), kind=kind, target=target)
            return CircuitState(
                org_id=row[0], kind=row[1], target=row[2], state=row[3],
                consecutive_fail=int(row[4]), consecutive_ok=int(row[5]),
                last_failure_at=row[6], last_success_at=row[7],
                opened_at=row[8], next_probe_at=row[9],
                failure_threshold=int(row[10]),
                cooldown_seconds=int(row[11]),
            )

    async def record_success(self, org_id, kind, target):
        from sqlalchemy import text
        async with self._sm() as s:
            await s.execute(text("""
                INSERT INTO smms.circuit_state
                  (org_id, target_kind, target_id, state,
                   consecutive_fail, consecutive_ok,
                   last_success_at)
                VALUES (:org, :k, :t, 'closed', 0, 1, now())
                ON CONFLICT (org_id, target_kind, target_id) DO UPDATE SET
                    state='closed',
                    consecutive_fail=0,
                    consecutive_ok=smms.circuit_state.consecutive_ok + 1,
                    last_success_at=now(),
                    opened_at=NULL,
                    next_probe_at=NULL
            """), {"org": str(org_id), "k": kind, "t": target})
            await s.commit()

    async def record_failure(self, org_id, kind, target, *, error=None):
        from sqlalchemy import text
        async with self._sm() as s:
            await s.execute(text("""
                INSERT INTO smms.circuit_state
                  (org_id, target_kind, target_id, state,
                   consecutive_fail, consecutive_ok,
                   last_failure_at)
                VALUES (:org, :k, :t, 'closed', 1, 0, now())
                ON CONFLICT (org_id, target_kind, target_id) DO UPDATE SET
                    consecutive_fail=smms.circuit_state.consecutive_fail + 1,
                    consecutive_ok=0,
                    last_failure_at=now(),
                    state = CASE
                      WHEN smms.circuit_state.consecutive_fail + 1
                           >= smms.circuit_state.failure_threshold
                      THEN 'open' ELSE smms.circuit_state.state END,
                    opened_at = CASE
                      WHEN smms.circuit_state.consecutive_fail + 1
                           >= smms.circuit_state.failure_threshold
                      THEN now() ELSE smms.circuit_state.opened_at END,
                    next_probe_at = CASE
                      WHEN smms.circuit_state.consecutive_fail + 1
                           >= smms.circuit_state.failure_threshold
                      THEN now()
                           + (interval '1 second'
                              * smms.circuit_state.cooldown_seconds)
                      ELSE smms.circuit_state.next_probe_at END
            """), {"org": str(org_id), "k": kind, "t": target})
            await s.commit()

    async def _set_half_open(self, org_id, kind, target):
        from sqlalchemy import text
        async with self._sm() as s:
            await s.execute(text("""
                UPDATE smms.circuit_state
                   SET state='half_open'
                 WHERE org_id=:org AND target_kind=:k AND target_id=:t
            """), {"org": str(org_id), "k": kind, "t": target})
            await s.commit()
