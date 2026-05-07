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
