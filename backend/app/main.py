"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.api.middleware import RequestContextMiddleware, SimpleRateLimitMiddleware
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.infrastructure.observability.tracing import configure_tracing
from app.plugins.manager import PluginManager

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.observability.log_level,
                      json=settings.observability.log_json)
    PluginManager().load_all()
    configure_tracing(app, settings)
    log.info("app_startup", env=settings.env, prefix=settings.api_prefix)
    yield
    log.info("app_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.api_title,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        SimpleRateLimitMiddleware,
        requests_per_minute=settings.security.rate_limit_per_minute,
    )

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
