"""OpenTelemetry tracing — graceful no-op when otel libs aren't present.

Why no-op fallback: not every dev machine has the OTel collector. The
helpers here detect whether ``opentelemetry-api`` is installed; if not,
they return a tiny dummy span so application code doesn't have to
guard every call site.

Usage:

    from app.core.tracing import trace_span, current_run_id

    async def handler(...):
        with trace_span("workflow.publish", run_id=run_id,
                        org_id=org_id, platform="linkedin") as span:
            span.set_attr("post_id", str(post.id))
            ...

The exporter is configured via environment:
    OTEL_EXPORTER_OTLP_ENDPOINT  — collector endpoint (gRPC or HTTP)
    OTEL_SERVICE_NAME            — defaults to ``smms-backend``

When the env vars aren't set we still register the SDK with a console
exporter so dev mode shows the spans. The SDK is initialised exactly
once via ``init_tracing()``, called from main.py at app startup.
"""
from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator

try:                                                                  # noqa: SIM105
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor, ConsoleSpanExporter,
    )
    OTEL_AVAILABLE = True
except Exception:                                                     # noqa: BLE001
    OTEL_AVAILABLE = False
    _otel_trace = None                                                # type: ignore[assignment]


_TRACER = None
_INITIALISED = False


def init_tracing(*, service_name: str | None = None) -> None:
    """Idempotent SDK setup. Safe to call multiple times — second call
    is a no-op. Picks up exporter from env, falls back to console."""
    global _TRACER, _INITIALISED
    if _INITIALISED or not OTEL_AVAILABLE:
        return
    name = service_name or os.environ.get("OTEL_SERVICE_NAME", "smms-backend")
    resource = Resource.create({"service.name": name})
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    exporter = None
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        except Exception:                                             # noqa: BLE001
            exporter = None
    if exporter is None and os.environ.get("OTEL_CONSOLE", "").lower() in ("1", "true"):
        exporter = ConsoleSpanExporter()
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))

    _otel_trace.set_tracer_provider(provider)
    _TRACER = _otel_trace.get_tracer("smms")
    _INITIALISED = True


class _NoopSpan:
    def set_attr(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attrs: dict[str, Any] | None = None) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass


class _SpanWrapper:
    """Adapts the OTel span to the same surface as _NoopSpan so call
    sites never need to know which implementation is active."""

    def __init__(self, span):
        self._span = span

    def set_attr(self, key: str, value: Any) -> None:
        try:
            self._span.set_attribute(key, value)
        except Exception:                                             # noqa: BLE001
            pass

    def add_event(self, name: str, attrs: dict[str, Any] | None = None) -> None:
        try:
            self._span.add_event(name, attributes=dict(attrs or {}))
        except Exception:                                             # noqa: BLE001
            pass

    def record_exception(self, exc: BaseException) -> None:
        try:
            self._span.record_exception(exc)
        except Exception:                                             # noqa: BLE001
            pass


@contextlib.contextmanager
def trace_span(name: str, **attrs: Any) -> Iterator[Any]:
    """Open a span. Always returns a span-like object — never None."""
    if not OTEL_AVAILABLE or _TRACER is None:
        yield _NoopSpan()
        return
    with _TRACER.start_as_current_span(name) as span:
        try:
            for k, v in (attrs or {}).items():
                if v is None:
                    continue
                try:
                    span.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
                except Exception:                                     # noqa: BLE001
                    pass
        except Exception:                                             # noqa: BLE001
            pass
        yield _SpanWrapper(span)


def current_run_id() -> str | None:
    """Best-effort retrieval — useful for cross-cutting log lines that
    want to print the active run id. Reads the span attribute set by
    upstream callers."""
    if not OTEL_AVAILABLE:
        return None
    try:
        span = _otel_trace.get_current_span()
        return getattr(span, "attributes", {}).get("run.id")
    except Exception:                                                 # noqa: BLE001
        return None
