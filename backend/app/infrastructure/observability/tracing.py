"""OpenTelemetry tracing setup. Skipped silently if OTLP endpoint is not set."""
from __future__ import annotations

from app.core.config import Settings, get_settings


def configure_tracing(app=None, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    if not s.observability.otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        return

    provider = TracerProvider(resource=Resource.create({
        "service.name": s.observability.service_name,
        "deployment.environment": s.env,
    }))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=s.observability.otlp_endpoint, insecure=True)
    ))
    trace.set_tracer_provider(provider)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)

    # Pillar 2 — also bind our core/tracing.py helper so trace_span()
    # call sites pick up the same provider for non-FastAPI spans
    # (workers, durable runner phases, agent calls).
    try:
        from app.core import tracing as core_tracing
        core_tracing._INITIALISED = True            # type: ignore[attr-defined]
        core_tracing._TRACER = trace.get_tracer("smms")  # type: ignore[attr-defined]
        core_tracing.OTEL_AVAILABLE = True
    except Exception:                                                 # noqa: BLE001
        pass
