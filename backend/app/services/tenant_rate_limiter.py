"""Per-tenant token bucket — coarse-grained queue priority limiter.

This is **complementary** to the existing per-(plugin, account) rate-limit
governor:

    * The plugin governor protects platform APIs (don't exceed LinkedIn's
      300 calls/minute per page) — narrow, specific, mostly read by
      adapters before publish.
    * This service protects the **queue** (one tenant can't enqueue
      10,000 jobs and starve everybody else's runs) — broad, coarse,
      consulted by the orchestrator before enqueueing a phase.

Algorithm: classic token bucket with last_refill tracking. ``capacity``
and ``refill_per_sec`` are per-org and may be overridden by the existing
tenant rate-limit override mechanism (``smms.tenant_rate_overrides``).
Default: 60 tokens at 1 token/sec — enough for human-scale workflows,
not enough to flood the queue.

Returns ``True`` from ``try_consume()`` if a token was deducted, else
``False``. Caller decides what to do — typically: log, delay, or
return 429 to the API caller.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


class TenantRateLimiter(ABC):
    @abstractmethod
    async def try_consume(self, org_id: str, *, cost: float = 1.0) -> bool: ...

    @abstractmethod
    async def status(self, org_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def configure(
        self, org_id: str, *,
        capacity: float | None = None,
        refill_per_sec: float | None = None,
    ) -> None: ...


# ── memory ─────────────────────────────────────────────────────────────
class InMemoryTenantRateLimiter(TenantRateLimiter):
    def __init__(
        self, *, default_capacity: float = 60.0,
        default_refill: float = 1.0,
    ) -> None:
        self._buckets: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._default_capacity = float(default_capacity)
        self._default_refill = float(default_refill)

    def _ensure(self, org_id: str) -> dict[str, Any]:
        b = self._buckets.get(org_id)
        if b is None:
            now = datetime.now(timezone.utc)
            b = {
                "tokens": self._default_capacity,
                "capacity": self._default_capacity,
                "refill_per_sec": self._default_refill,
                "last_refill_at": now,
            }
            self._buckets[org_id] = b
        return b

    def _refill(self, b: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        elapsed = (now - b["last_refill_at"]).total_seconds()
        if elapsed > 0:
            b["tokens"] = min(
                b["capacity"],
                b["tokens"] + elapsed * b["refill_per_sec"],
            )
            b["last_refill_at"] = now

    async def try_consume(self, org_id, *, cost=1.0):
        async with self._lock:
            b = self._ensure(str(org_id))
            self._refill(b)
            if b["tokens"] >= cost:
                b["tokens"] -= cost
                return True
            return False

    async def status(self, org_id):
        async with self._lock:
            b = self._ensure(str(org_id))
            self._refill(b)
            return {
                "tokens": float(b["tokens"]),
                "capacity": float(b["capacity"]),
                "refill_per_sec": float(b["refill_per_sec"]),
            }

    async def configure(self, org_id, *, capacity=None, refill_per_sec=None):
        async with self._lock:
            b = self._ensure(str(org_id))
            if capacity is not None:
                b["capacity"] = float(capacity)
                b["tokens"] = min(b["tokens"], b["capacity"])
            if refill_per_sec is not None:
                b["refill_per_sec"] = float(refill_per_sec)


# ── postgres ───────────────────────────────────────────────────────────
class PostgresTenantRateLimiter(TenantRateLimiter):
    def __init__(self, session_maker, *,
                 default_capacity: float = 60.0,
                 default_refill: float = 1.0) -> None:
        self._sm = session_maker
        self._default_capacity = float(default_capacity)
        self._default_refill = float(default_refill)

    async def try_consume(self, org_id, *, cost=1.0):
        from sqlalchemy import text
        async with self._sm() as s:
            # Single-statement compare-and-decrement so concurrent
            # workers can't both consume the last token.
            r = await s.execute(text("""
                INSERT INTO smms.tenant_rate_state
                  (org_id, bucket_tokens, bucket_capacity,
                   refill_per_sec, last_refill_at)
                VALUES (:org, :cap, :cap, :rps, now())
                ON CONFLICT (org_id) DO UPDATE SET
                    bucket_tokens = LEAST(
                        smms.tenant_rate_state.bucket_capacity,
                        smms.tenant_rate_state.bucket_tokens
                          + EXTRACT(EPOCH FROM (now()
                            - smms.tenant_rate_state.last_refill_at))
                          * smms.tenant_rate_state.refill_per_sec
                    ) - CASE
                        WHEN LEAST(
                            smms.tenant_rate_state.bucket_capacity,
                            smms.tenant_rate_state.bucket_tokens
                              + EXTRACT(EPOCH FROM (now()
                                - smms.tenant_rate_state.last_refill_at))
                              * smms.tenant_rate_state.refill_per_sec
                        ) >= :cost THEN :cost ELSE 0 END,
                    last_refill_at = now()
                RETURNING bucket_tokens, bucket_capacity, :cost::float8 AS cost,
                  (bucket_tokens >= 0) AS allowed
            """), {"org": str(org_id), "cap": self._default_capacity,
                   "rps": self._default_refill, "cost": float(cost)})
            row = r.first()
            await s.commit()
            return bool(row[3]) if row else False

    async def status(self, org_id):
        from sqlalchemy import text
        async with self._sm() as s:
            r = await s.execute(text("""
                SELECT bucket_tokens, bucket_capacity, refill_per_sec
                  FROM smms.tenant_rate_state WHERE org_id=:org
            """), {"org": str(org_id)})
            row = r.first()
            if not row:
                return {
                    "tokens": self._default_capacity,
                    "capacity": self._default_capacity,
                    "refill_per_sec": self._default_refill,
                }
            return {"tokens": float(row[0]), "capacity": float(row[1]),
                    "refill_per_sec": float(row[2])}

    async def configure(self, org_id, *, capacity=None, refill_per_sec=None):
        from sqlalchemy import text
        async with self._sm() as s:
            await s.execute(text("""
                INSERT INTO smms.tenant_rate_state
                  (org_id, bucket_tokens, bucket_capacity, refill_per_sec)
                VALUES (:org, :cap, :cap, :rps)
                ON CONFLICT (org_id) DO UPDATE SET
                    bucket_capacity = COALESCE(:cap, smms.tenant_rate_state.bucket_capacity),
                    bucket_tokens = LEAST(
                        smms.tenant_rate_state.bucket_tokens,
                        COALESCE(:cap, smms.tenant_rate_state.bucket_capacity)),
                    refill_per_sec = COALESCE(:rps, smms.tenant_rate_state.refill_per_sec)
            """), {
                "org": str(org_id),
                "cap": float(capacity) if capacity is not None else None,
                "rps": float(refill_per_sec) if refill_per_sec is not None else None,
            })
            await s.commit()
