"""Liveness + readiness probes for orchestration platforms (Render, k8s, ECS).

* ``GET /health`` — liveness. Returns 200 as long as the process can serve a
  request. Don't add dependency checks here — orchestrators kill the pod
  on failure, so a transient DB blip would bounce every replica.
* ``GET /ready`` — readiness. Pings Postgres + Redis. Returns 503 if any
  required dependency is down so the load balancer takes the pod out of
  rotation without killing it. Use this as Render's ``healthCheckPath``.

Both probes are exempted from the rate-limit middleware (see
:mod:`app.api.middleware`).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Response, status

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
async def metrics() -> Response:
    """Pillar 2 — exposes the Prometheus exposition format. Configure
    your Prometheus job to scrape ``/metrics`` (no auth; same trust
    boundary as readiness). When prometheus_client isn't installed we
    return a comment so scrapers don't error out."""
    from app.core.metrics import render_metrics
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


@router.get("/ready", summary="Readiness probe")
async def ready(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    checks: dict[str, dict] = {}
    started = time.perf_counter()

    # Run Postgres + Redis probes in parallel — both bounded to ~2 seconds
    # so a hung dependency can't make the entire endpoint hang.
    pg_task = asyncio.create_task(_check_postgres(settings))
    rd_task = asyncio.create_task(_check_redis(settings))
    pg_result, rd_result = await asyncio.gather(pg_task, rd_task)

    checks["postgres"] = pg_result
    checks["redis"] = rd_result

    overall_ok = all(c.get("ok") or c.get("skipped") for c in checks.values())
    response.status_code = (
        status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return {
        "status": "ready" if overall_ok else "degraded",
        "checks": checks,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


async def _check_postgres(settings: Settings) -> dict[str, Any]:
    """SELECT 1 against the configured Postgres URL. Skipped in memory mode."""
    if settings.resolved_persistence_backend() != "supabase":
        return {"ok": True, "skipped": True, "reason": "persistence=memory"}
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        engine = create_async_engine(
            settings.db_url(),
            connect_args=settings.db_connect_args(),
            pool_pre_ping=False,
            pool_size=1,
            max_overflow=0,
        )
        async with asyncio.timeout(2.0):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return {"ok": True}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout"}
    except Exception as exc:                                            # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


async def _check_redis(settings: Settings) -> dict[str, Any]:
    """PING the configured Redis URL. Skipped if no URL is set."""
    url = (settings.redis.url or "").strip()
    if not url:
        return {"ok": True, "skipped": True, "reason": "no REDIS_URL"}
    try:
        import redis.asyncio as redis_async
        client = redis_async.from_url(url, socket_connect_timeout=2.0)
        try:
            async with asyncio.timeout(2.0):
                pong = await client.ping()
            return {"ok": bool(pong)}
        finally:
            try:
                await client.close()
            except Exception:                                            # noqa: BLE001
                pass
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout"}
    except Exception as exc:                                            # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
