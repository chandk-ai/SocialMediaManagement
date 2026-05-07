"""FastAPI application entry point."""
from __future__ import annotations

import re
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.api.middleware import RequestContextMiddleware, SimpleRateLimitMiddleware
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.infrastructure.observability.tracing import configure_tracing
from app.plugins.manager import PluginManager

log = get_logger(__name__)


def _redact_db_url(url: str) -> str:
    """Mask the password component for safe logging."""
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url or "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.observability.log_level,
                      json=settings.observability.log_json)
    PluginManager().load_all()
    configure_tracing(app, settings)
    log.info(
        "app_startup",
        env=settings.env,
        prefix=settings.api_prefix,
        persistence_backend=settings.resolved_persistence_backend(),
        db_url=_redact_db_url(settings.db_url()),
        db_connect_args=settings.db_connect_args(),
        supabase_url=settings.supabase.url,
    )
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

    # Surface real exception details in the JSON response body so the frontend
    # banner can show a useful message (instead of the proxy's "Unknown error").
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "unhandled_exception",
            path=request.url.path,
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
            traceback=traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"{type(exc).__name__}: {exc}",
            },
        )

    return app


app = create_app()
