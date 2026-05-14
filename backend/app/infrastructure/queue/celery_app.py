"""Celery application factory.

Workers run agent pipelines; the API layer stays responsive by enqueueing.
"""
from __future__ import annotations

import os

from celery import Celery
from celery.signals import beat_init, worker_process_init

from app.core.config import get_settings
from app.infrastructure.observability.sentry import configure_sentry


def _mark_celery_context() -> None:
    """Stamp ``CELERY_WORKER_RUNNING=1`` so ``app.api.deps._build_repos`` knows
    it should use ``NullPool`` for the AsyncEngine.

    Why this matters — the asyncio loop-reuse bug:
        Celery tasks call ``asyncio.run(...)`` which creates a *fresh* event
        loop, runs the coroutine, then closes the loop. ``_build_repos`` is
        ``@lru_cache``'d so the AsyncEngine + asyncpg connection pool persists
        across tasks. The asyncpg connections still hold references to the
        previous (closed) loop → next task gets
        ``RuntimeError: got Future attached to a different loop``.

        ``NullPool`` creates a brand-new connection per session and disposes
        on close — nothing survives across ``asyncio.run`` boundaries.

    Argv-sniffing in ``_is_celery_context`` is a fallback; this env var is the
    authoritative signal."""
    os.environ["CELERY_WORKER_RUNNING"] = "1"


@worker_process_init.connect
def _init_sentry_in_worker(**_: object) -> None:
    """Each Celery worker process needs its own Sentry init — the SDK lives in
    process-local state and isn't inherited by forks. No-op when SENTRY_DSN is
    unset."""
    _mark_celery_context()
    configure_sentry()


@beat_init.connect
def _init_sentry_in_beat(**_: object) -> None:
    """Beat is a separate process from the workers; init independently."""
    _mark_celery_context()
    configure_sentry()


# Also mark at import time — covers the case where workers haven't yet forked
# (e.g. solo pool, eager mode, prefork master process loading tasks). The
# signal handlers above still re-stamp post-fork to be safe.
if any("celery" in arg.lower() for arg in os.sys.argv):
    _mark_celery_context()


def make_celery() -> Celery:
    s = get_settings()
    app = Celery(
        "smms",
        broker=s.queue.broker_url,
        backend=s.queue.result_backend,
        include=[
            "app.workers.workflow_runner",
            "app.workers.scheduler",
            "app.workers.publish",
            "app.workers.metrics_collector",
        ],
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        worker_max_tasks_per_child=200,
        task_default_queue="default",
        task_routes={
            "app.workers.workflow_runner.run_workflow": {"queue": "workflows"},
            "app.workers.publish.publish_post":          {"queue": "publish"},
            "app.workers.publish.publish_post_dlq":      {"queue": "publish_dlq"},
            "app.workers.scheduler.tick_scheduler":      {"queue": "default"},
            "app.workers.metrics_collector.collect_all_metrics":
                                                          {"queue": "default"},
        },
    )
    return app


celery_app = make_celery()
