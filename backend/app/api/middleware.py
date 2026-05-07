"""Cross-cutting HTTP middleware: request ID, timing, rate limit."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, request_id_var

log = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(rid)
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


class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
    """Tiny in-memory sliding-window limiter. Production should use Redis."""

    def __init__(self, app, *, requests_per_minute: int) -> None:
        super().__init__(app)
        self._cap = requests_per_minute
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        key = request.client.host if request.client else "anon"
        now = time.time()
        bucket = self._buckets[key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= self._cap:
            return Response(status_code=429, content="rate limit exceeded")
        bucket.append(now)
        return await call_next(request)
