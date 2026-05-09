"""Prometheus metrics — graceful no-op when prometheus_client is missing.

Exposes the canonical metrics shape we want to scrape:

    smms_runs_started_total{org,workflow}
    smms_runs_completed_total{org,workflow,status}
    smms_run_duration_seconds{phase}             (histogram)
    smms_jobs_enqueued_total{kind}
    smms_jobs_processed_total{kind,status}
    smms_job_duration_seconds{kind}              (histogram)
    smms_publish_total{platform,status}
    smms_publish_duration_seconds{platform}      (histogram)
    smms_circuit_state{kind,target}              (gauge: 0 closed, 1 open, 2 half_open)
    smms_llm_tokens_total{provider,model,kind}
    smms_llm_cost_usd_total{provider,model}
    smms_engagement_fetch_total{platform,status}

Wire-up:
    from app.core.metrics import M
    M.runs_started.labels(org=org_id, workflow=wf_id).inc()
    with M.run_duration.labels(phase="execute").time():
        ...

Each label set is created lazily by .labels(); the registry is global.
"""
from __future__ import annotations

import contextlib
from typing import Any

try:                                                                  # noqa: SIM105
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter, Gauge, Histogram, REGISTRY,
        generate_latest,
    )
    PROM_AVAILABLE = True
except Exception:                                                     # noqa: BLE001
    PROM_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
    REGISTRY = None  # type: ignore[assignment]


class _NoopCounter:
    def labels(self, **kw: Any) -> "_NoopCounter":
        return self
    def inc(self, amount: float = 1.0) -> None:
        pass


class _NoopGauge(_NoopCounter):
    def set(self, value: float) -> None:
        pass


class _NoopHistogram(_NoopCounter):
    @contextlib.contextmanager
    def time(self):
        yield
    def observe(self, v: float) -> None:
        pass


def _counter(name: str, doc: str, labels: list[str] | None = None):
    if PROM_AVAILABLE:
        try:
            return Counter(name, doc, labels or [])
        except Exception:                                             # noqa: BLE001
            pass
    return _NoopCounter()


def _gauge(name: str, doc: str, labels: list[str] | None = None):
    if PROM_AVAILABLE:
        try:
            return Gauge(name, doc, labels or [])
        except Exception:                                             # noqa: BLE001
            pass
    return _NoopGauge()


def _histogram(name: str, doc: str, labels: list[str] | None = None,
                buckets: tuple[float, ...] | None = None):
    if PROM_AVAILABLE:
        try:
            kw: dict[str, Any] = {}
            if buckets:
                kw["buckets"] = buckets
            return Histogram(name, doc, labels or [], **kw)
        except Exception:                                             # noqa: BLE001
            pass
    return _NoopHistogram()


# Standard latency buckets — covers 5ms to ~5min.
_LAT = (0.005, 0.025, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0)


class _Metrics:
    runs_started = _counter("smms_runs_started_total",
                            "Workflow runs started", ["org", "workflow"])
    runs_completed = _counter("smms_runs_completed_total",
                              "Workflow runs completed",
                              ["org", "workflow", "status"])
    run_duration = _histogram("smms_run_duration_seconds",
                              "Per-phase duration", ["phase"], _LAT)
    jobs_enqueued = _counter("smms_jobs_enqueued_total",
                              "Jobs enqueued", ["kind"])
    jobs_processed = _counter("smms_jobs_processed_total",
                               "Jobs finished (succ/fail/dead)",
                               ["kind", "status"])
    job_duration = _histogram("smms_job_duration_seconds",
                              "Job dispatch duration",
                              ["kind"], _LAT)
    publish_total = _counter("smms_publish_total",
                             "Publish attempts",
                             ["platform", "status"])
    publish_duration = _histogram("smms_publish_duration_seconds",
                                   "Publish duration", ["platform"], _LAT)
    circuit_state = _gauge("smms_circuit_state",
                           "0=closed, 1=open, 2=half_open",
                           ["kind", "target"])
    llm_tokens = _counter("smms_llm_tokens_total",
                          "LLM tokens consumed",
                          ["provider", "model", "kind"])
    llm_cost_usd = _counter("smms_llm_cost_usd_total",
                            "LLM cost (USD)",
                            ["provider", "model"])
    engagement_fetch = _counter("smms_engagement_fetch_total",
                                 "Engagement fetch attempts",
                                 ["platform", "status"])
    selection_chosen = _counter("smms_selection_chosen_total",
                                 "Items chosen by selector",
                                 ["strategy"])


M = _Metrics()


def render_metrics() -> tuple[bytes, str]:
    """Serialize current metrics to the wire format. Returns
    (body_bytes, content_type)."""
    if not PROM_AVAILABLE or REGISTRY is None:
        body = b"# prometheus_client not installed\n"
        return body, "text/plain; version=0.0.4"
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
