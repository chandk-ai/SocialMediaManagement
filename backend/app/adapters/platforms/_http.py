"""Shared HTTP helper used by every platform adapter.

Provides a single async client with retries + circuit breaker per platform.
Platform adapters compose this rather than depending directly on httpx.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.resilience import CircuitBreaker


class PlatformHttp:
    def __init__(self, base_url: str, *, name: str, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self.breaker = CircuitBreaker(name=f"http:{name}")

    async def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        async def _call() -> httpx.Response:
            r = await self._client.request(method, path, **kw)
            r.raise_for_status()
            return r
        return await self.breaker.call(_call)

    async def aclose(self) -> None:
        await self._client.aclose()
