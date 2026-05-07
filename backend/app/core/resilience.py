"""Resilience primitives for external calls (LLM, social APIs, sources).

We use tenacity for retries and a tiny self-contained circuit breaker so
adapters get production-grade behaviour without a heavyweight library.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, TypeVar

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout_sec: float = 30.0
    half_open_max_calls: int = 1


class CircuitBreaker:
    def __init__(self, name: str, cfg: CircuitBreakerConfig | None = None) -> None:
        self.name = name
        self.cfg = cfg or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.cfg.recovery_timeout_sec:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
        return self._state

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            current = self.state
            if current is CircuitState.OPEN:
                raise CircuitOpenError(f"Circuit '{self.name}' is OPEN")
            if current is CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.cfg.half_open_max_calls:
                    raise CircuitOpenError(f"Circuit '{self.name}' HALF_OPEN cap reached")
                self._half_open_calls += 1

        try:
            result = await fn()
        except Exception:
            await self._on_failure()
            raise
        else:
            await self._on_success()
            return result

    async def _on_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._opened_at = None

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._failures >= self.cfg.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


class CircuitOpenError(Exception):
    pass


def with_retry(
    *exception_types: type[BaseException],
    attempts: int = 3,
    multiplier: float = 0.5,
    max_wait: float = 8.0,
) -> AsyncRetrying:
    """Standard exponential-backoff retry decorator-builder for adapters."""
    return AsyncRetrying(
        retry=retry_if_exception_type(exception_types or (Exception,)),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=multiplier, max=max_wait),
        reraise=True,
    )
