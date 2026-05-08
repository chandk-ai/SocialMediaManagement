"""Cross-cutting HTTP middleware: request ID, timing, rate limit.

The rate limiter has two important properties:

1. **Per-org keys when possible** — we crack the bearer token and key the
   bucket by ``org_id`` so a single tenant can't starve everyone else.
   Unauthenticated requests (login, webhooks, healthcheck) still get
   limited per-IP.
2. **Redis-backed when ``REDIS_URL`` is set** — that gives correct counts
   across multiple Render web workers / pods. Otherwise we fall back to a
   process-local sliding window so dev / tests keep working.

The shared Lua script lives in :mod:`app.core.rate_limit`; we reuse the
same Redis client for HTTP rate limiting and per-account publish-side
governance to keep the connection-count low.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import get_logger, request_id_var
from app.infrastructure.observability.sentry import set_request_context

log = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        # Attach the request id to Sentry up-front so any error captured during
        # auth / dependency resolution still carries it. user_id / org_id are
        # added later by the `current_user` dependency once we've decoded the
        # bearer token (see app/core/security.py).
        set_request_context(request_id=rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            log.info("http_request",
                     method=request.method, path=request.url.path,
                     duration_ms=round(elapsed, 2))
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


# Same Lua script the publish-path governor uses. Inlined here so the
# middleware doesn't depend on RateLimitGovernor's wait/sleep semantics —
# HTTP requests should be rejected immediately, not held in flight.
_LUA_TOKEN_BUCKET = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local data = redis.call("HMGET", key, "tokens", "ts")
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts = now
end
local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * refill)
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait = (1 - tokens) / refill
  tokens = 0
end
redis.call("HMSET", key, "tokens", tokens, "ts", now)
redis.call("EXPIRE", key, 3600)
return tostring(wait)
"""


class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-org (or per-IP) token-bucket limiter.

    * If the bearer token decodes to an ``org_id`` claim, that's the bucket key.
    * Otherwise we fall back to client IP — this catches webhook traffic and
      auth-less paths.
    * Redis is the backing store when ``REDIS_URL`` is reachable; if not, we
      keep an in-memory sliding window so the dev server still rate-limits.

    The capacity and refill rate are derived from
    ``SecuritySettings.rate_limit_per_minute``: we treat the configured value
    as both the bucket size (allow short bursts up to the per-minute cap) and
    the per-second refill rate (smoothed average).
    """

    def __init__(self, app, *, requests_per_minute: int, redis_url: str | None = None) -> None:
        super().__init__(app)
        self._cap = max(1, int(requests_per_minute))
        self._refill = self._cap / 60.0
        self._redis_url = redis_url
        self._redis: Any | None = None
        self._lua_sha: str | None = None
        self._redis_failed = False
        # In-memory fallback: sliding window of timestamps per key.
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    # ── key derivation ──────────────────────────────────────────────────
    @staticmethod
    def _decode_org_id(authorization: str | None) -> str | None:
        """Best-effort JWT payload decode — we DON'T verify the signature
        here. The bucket only needs a stable identifier; if a malicious
        client forges an org_id they'd just rate-limit themselves harder.
        Real auth happens in the route's `current_user` dependency."""
        if not authorization or not authorization.startswith("Bearer "):
            return None
        token = authorization[len("Bearer "):].strip()
        # Dev-mode tokens look like `dev.<role>.<email>.<org_id>`.
        if token.startswith("dev."):
            parts = token.split(".")
            return parts[3] if len(parts) > 3 else None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        try:
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
        except Exception:                                               # noqa: BLE001
            return None
        return (
            payload.get("org_id")
            or payload.get("https://smms/org_id")
            or (payload.get("app_metadata") or {}).get("org_id")
        )

    def _bucket_key(self, request: Request) -> str:
        org_id = self._decode_org_id(request.headers.get("authorization"))
        if org_id:
            return f"rl:http:org:{org_id}"
        ip = request.client.host if request.client else "anon"
        return f"rl:http:ip:{ip}"

    # ── dispatch ────────────────────────────────────────────────────────
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for the metrics scrape and health endpoints —
        # these are polled aggressively by Render / Prometheus.
        path = request.url.path
        if path in {"/metrics", "/api/v1/health", "/api/v1/health/ready"}:
            return await call_next(request)

        key = self._bucket_key(request)
        wait = await self._compute_wait(key)
        if wait > 0:
            retry_after = max(1, int(wait + 0.5))
            log.info("rate_limited", key=key, retry_in=round(wait, 2))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "rate limit exceeded",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    async def _compute_wait(self, key: str) -> float:
        # Try Redis first; fall back to in-memory if unreachable.
        if self._redis_url and not self._redis_failed:
            try:
                if self._redis is None:
                    import redis.asyncio as redis_async
                    self._redis = redis_async.from_url(self._redis_url)
                if self._lua_sha is None:
                    self._lua_sha = await self._redis.script_load(_LUA_TOKEN_BUCKET)
                res = await self._redis.evalsha(
                    self._lua_sha, 1, key,
                    self._cap, self._refill, time.time(),
                )
                return float(res)
            except Exception as exc:                                    # noqa: BLE001
                # Once Redis fails, stay on in-memory for the rest of the
                # process lifetime — flapping back and forth is worse than
                # consistent local-only counting.
                if not self._redis_failed:
                    log.warning("rate_limit_redis_unavailable", error=str(exc))
                self._redis_failed = True
                self._redis = None
        return self._inmem_wait(key)

    def _inmem_wait(self, key: str) -> float:
        now = time.time()
        bucket = self._buckets[key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= self._cap:
            # Wait until the oldest timestamp falls out of the 60s window.
            return max(0.0, 60.0 - (now - bucket[0]))
        bucket.append(now)
        return 0.0
