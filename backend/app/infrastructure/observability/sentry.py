"""Sentry initialization — error monitoring + performance tracing.

Idempotent and lazy: ``configure_sentry()`` is safe to call from any process
entry point (FastAPI app, Celery worker, beat scheduler). When ``SENTRY_DSN``
is unset, every code path here turns into a no-op so dev / test environments
never accidentally send data.

Why we keep PII off by default:
- We tag the user via ``user_id`` and ``org_id`` only — never email or name.
- This satisfies most enterprise policies and is enough for Sentry's group
  filters; flip ``SENTRY_SEND_DEFAULT_PII=true`` if you explicitly want more.

The initialiser also wires in helpers for setting per-request tags, which
``RequestContextMiddleware`` calls so every event carries request_id + the
authenticated principal when one is present.
"""
from __future__ import annotations

from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_INITIALIZED = False


def configure_sentry(settings: Settings | None = None) -> bool:
    """Init the Sentry SDK once per process. Returns True if Sentry is live.

    Safe to call multiple times — subsequent calls become no-ops. Returns
    False if the SDK isn't installed, the DSN is empty, or init raises
    (which we never want to crash the app over)."""
    global _INITIALIZED
    if _INITIALIZED:
        return True

    s = settings or get_settings()
    dsn = (s.sentry.dsn or "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        log.warning("sentry_sdk_not_installed_skipping")
        return False

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=s.sentry.environment or s.env,
            release=s.sentry.release,
            traces_sample_rate=s.sentry.traces_sample_rate,
            profiles_sample_rate=s.sentry.profiles_sample_rate,
            send_default_pii=s.sentry.send_default_pii,
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
            # Tag every event with the service name so cross-service dashboards
            # can split api / worker / beat without spelunking the breadcrumbs.
            attach_stacktrace=False,
            in_app_include=["app"],
        )
        sentry_sdk.set_tag("service", s.observability.service_name)
        _INITIALIZED = True
        log.info(
            "sentry_initialized",
            environment=s.sentry.environment or s.env,
            traces_sample_rate=s.sentry.traces_sample_rate,
        )
        return True
    except Exception as exc:                                            # noqa: BLE001
        log.warning("sentry_init_failed", error=str(exc))
        return False


def is_initialized() -> bool:
    return _INITIALIZED


def set_request_context(
    *,
    request_id: str | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
    role: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Attach per-request tags to the current Sentry scope.

    No-ops when Sentry isn't initialised so callers don't need to guard.
    """
    if not _INITIALIZED:
        return
    try:
        import sentry_sdk
        scope = sentry_sdk.get_isolation_scope()
        if request_id:
            scope.set_tag("request_id", request_id)
        if org_id:
            scope.set_tag("org_id", org_id)
        if role:
            scope.set_tag("role", role)
        if user_id:
            # Use the SDK's user model so Sentry's UI shows the affected user
            # row in event details. Email/name omitted on purpose.
            scope.set_user({"id": user_id})
        if extra:
            for k, v in extra.items():
                scope.set_extra(k, v)
    except Exception:                                                   # noqa: BLE001
        # Never let observability blow up the request path.
        pass


def capture_exception(exc: BaseException) -> None:
    """Convenience wrapper — explicit capture for exceptions we already handle
    but still want to see in Sentry (publish DLQ, agent failures, etc.)."""
    if not _INITIALIZED:
        return
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
    except Exception:                                                   # noqa: BLE001
        pass
