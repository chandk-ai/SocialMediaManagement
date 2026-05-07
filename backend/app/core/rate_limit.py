"""Per-(plugin, account) token-bucket rate limiter.

Used by the publish path so a single noisy account can't get the org's
LinkedIn / X / Meta tokens revoked. Backed by Redis (atomic Lua), with a
process-local fallback for dev.

Defaults are conservative — the platforms' actual limits are higher, but
we want headroom for retries.

Usage:
    governor = RateLimitGovernor()
    waited = await governor.acquire("linkedin", account_id="urn:li:person:…")
    # → returns the seconds we slept; 0 if the bucket had a token immediately
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RateBudget:
    capacity: int           # max tokens in the bucket
    refill_per_sec: float   # tokens added per second


# Conservative starting points — tune via observability.
PLATFORM_BUDGETS: dict[str, RateBudget] = {
    "linkedin":  RateBudget(capacity=5, refill_per_sec=5/60.0),         # ~5/min
    "twitter":   RateBudget(capacity=50, refill_per_sec=50/(15*60)),    # 50 / 15 min
    "facebook":  RateBudget(capacity=10, refill_per_sec=10/60.0),
    "instagram": RateBudget(capacity=10, refill_per_sec=10/60.0),
    "youtube":   RateBudget(capacity=2,  refill_per_sec=2/60.0),
    "tiktok":    RateBudget(capacity=2,  refill_per_sec=2/60.0),
    "threads":   RateBudget(capacity=5,  refill_per_sec=5/60.0),
    "pinterest": RateBudget(capacity=10, refill_per_sec=10/60.0),
    "reddit":    RateBudget(capacity=1,  refill_per_sec=1/60.0),         # 1/min — strict
    "mastodon":  RateBudget(capacity=10, refill_per_sec=10/60.0),
    "bluesky":   RateBudget(capacity=10, refill_per_sec=10/60.0),
    "medium":    RateBudget(capacity=5,  refill_per_sec=5/300.0),
    "discord":   RateBudget(capacity=20, refill_per_sec=20/60.0),
    "slack":     RateBudget(capacity=20, refill_per_sec=20/60.0),
    "telegram":  RateBudget(capacity=20, refill_per_sec=20/60.0),
    "tumblr":    RateBudget(capacity=10, refill_per_sec=10/60.0),
}
DEFAULT_BUDGET = RateBudget(capacity=10, refill_per_sec=10/60.0)


# ── Lua script: atomically refill + consume one token, return wait_seconds.
LUA = """
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


class RateLimitGovernor:
    """Async-friendly governor.

    Pass `redis_url=None` to use the in-memory fallback (only safe in a
    single-process deployment / tests).
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis = None
        self._lua_sha: str | None = None
        if redis_url:
            try:
                import redis.asyncio as redis
                self._redis = redis.from_url(redis_url)
            except ImportError:
                log.warning("redis_unavailable_using_memory_rate_limiter")
        # In-memory fallback state: {key: (tokens, ts)}
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self, plugin_name: str, *, account_id: str = "default",
        wait: bool = True,
    ) -> float:
        """Consume one token. Returns the seconds we slept (0 if immediate).

        If `wait=False` and no tokens are available, raises `RateLimited`."""
        budget = PLATFORM_BUDGETS.get(plugin_name, DEFAULT_BUDGET)
        key = f"rl:{plugin_name}:{account_id}"
        wait_seconds = await self._compute_wait(key, budget)
        if wait_seconds > 0:
            if not wait:
                raise RateLimited(plugin_name, account_id, wait_seconds)
            log.info("rate_limit_sleep", plugin=plugin_name,
                     account=account_id, seconds=round(wait_seconds, 2))
            await asyncio.sleep(wait_seconds)
        return wait_seconds

    async def _compute_wait(self, key: str, budget: RateBudget) -> float:
        if self._redis is not None:
            if self._lua_sha is None:
                self._lua_sha = await self._redis.script_load(LUA)
            res = await self._redis.evalsha(
                self._lua_sha, 1, key,
                budget.capacity, budget.refill_per_sec, time.time(),
            )
            return float(res)
        async with self._lock:
            tokens, ts = self._state.get(key, (budget.capacity, time.time()))
            now = time.time()
            elapsed = max(0.0, now - ts)
            tokens = min(budget.capacity, tokens + elapsed * budget.refill_per_sec)
            if tokens >= 1:
                tokens -= 1
                wait = 0.0
            else:
                wait = (1 - tokens) / budget.refill_per_sec
                tokens = 0
            self._state[key] = (tokens, now)
            return wait


class RateLimited(Exception):
    def __init__(self, plugin: str, account: str, retry_in: float) -> None:
        super().__init__(
            f"rate-limited on {plugin}:{account} — retry in {retry_in:.2f}s"
        )
        self.plugin = plugin
        self.account = account
        self.retry_in = retry_in


_global: RateLimitGovernor | None = None


def get_rate_governor() -> RateLimitGovernor:
    global _global
    if _global is None:
        from app.core.config import get_settings
        s = get_settings()
        _global = RateLimitGovernor(redis_url=s.redis.url)
    return _global
