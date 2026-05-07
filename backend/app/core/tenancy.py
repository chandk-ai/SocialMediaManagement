"""Tenant context — the single source of truth for "where does this org's
data live and how do we reach it".

Every request resolves into a `TenantContext` early in the dependency chain.
Repositories, storage, queue routing, secrets, and rate limiters all consult
this context, so an organisation can be promoted to dedicated infra by
flipping a few fields on its `Organization` row — no code changes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.domain.entities.organization import IsolationLevel, Organization
from app.domain.value_objects.ids import OrgId

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Resolved per-tenant configuration."""
    org_id: OrgId
    isolation_level: IsolationLevel
    region: str
    db_url: str
    storage_bucket: str
    storage_prefix: str           # folder inside the bucket
    encryption_key_id: str | None
    rate_limit_per_minute: int
    max_concurrent_runs: int
    plan: str
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_org(cls, org: Organization, settings: Settings | None = None) -> "TenantContext":
        s = settings or get_settings()
        if org.is_dedicated and org.dedicated_db_url:
            db_url = org.dedicated_db_url
        else:
            db_url = s.db_url()
        return cls(
            org_id=org.id,
            isolation_level=org.isolation_level,
            region=org.region,
            db_url=db_url,
            storage_bucket=s.supabase.storage_bucket,
            storage_prefix=org.storage_prefix or f"orgs/{org.id}",
            encryption_key_id=org.encryption_key_id,
            rate_limit_per_minute=(
                org.rate_limit_per_minute or s.security.rate_limit_per_minute
            ),
            max_concurrent_runs=org.max_concurrent_runs,
            plan=org.plan,
        )


class TenantAwareSessionFactory:
    """Hands out an async SQLAlchemy session-maker for the tenant.

    SHARED tenants reuse one process-wide engine.
    DEDICATED_DB / DEDICATED_STACK tenants get their own cached engine.
    """

    def __init__(self) -> None:
        self._shared = None       # built lazily
        self._per_tenant: dict[OrgId, Any] = {}
        self._lock = asyncio.Lock()

    async def for_tenant(self, ctx: TenantContext):
        # Lazy import so the module imports cleanly without sqlalchemy.
        try:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        except ImportError as exc:                          # pragma: no cover
            raise RuntimeError("sqlalchemy is required for DB sessions") from exc

        async with self._lock:
            if ctx.isolation_level is IsolationLevel.SHARED:
                if self._shared is None:
                    s = get_settings()
                    engine = create_async_engine(
                        s.db_url(),
                        pool_pre_ping=True,
                        pool_size=s.db.pool_size,
                        max_overflow=s.db.max_overflow,
                        echo=s.db.echo,
                    )
                    self._shared = async_sessionmaker(engine, expire_on_commit=False)
                    log.info("tenant_shared_engine_built", url=_redact(s.db_url()))
                return self._shared

            # Dedicated — one engine per tenant, cached.
            if ctx.org_id not in self._per_tenant:
                engine = create_async_engine(
                    ctx.db_url,
                    pool_pre_ping=True,
                    pool_size=5,
                    max_overflow=10,
                )
                self._per_tenant[ctx.org_id] = async_sessionmaker(
                    engine, expire_on_commit=False,
                )
                log.info("tenant_dedicated_engine_built",
                         org_id=str(ctx.org_id), url=_redact(ctx.db_url))
            return self._per_tenant[ctx.org_id]


_global_factory = TenantAwareSessionFactory()


def get_session_factory() -> TenantAwareSessionFactory:
    return _global_factory


def _redact(url: str) -> str:
    """Redact passwords from connection strings for safe logging."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.split("@", 1)
    if ":" in creds:
        user, _ = creds.split(":", 1)
        creds = f"{user}:***"
    return f"{scheme}://{creds}@{host}"
