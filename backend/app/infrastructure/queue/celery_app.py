"""Celery application factory.

Workers run agent pipelines; the API layer stays responsive by enqueueing.
"""
from __future__ import annotations

from celery import Celery
from celery.signals import beat_init, worker_process_init

from app.core.config import get_settings
from app.infrastructure.observability.sentry import configure_sentry


@worker_process_init.connect
def _init_sentry_in_worker(**_: object) -> None:
    """Each Celery worker process needs its own Sentry init — the SDK lives in
    process-local state and isn't inherited by forks. No-op when SENTRY_DSN is
    unset."""
    configure_sentry()


@beat_init.connect
def _init_sentry_in_beat(**_: object) -> None:
    """Beat is a separate process from the workers; init independently."""
    configure_sentry()


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
