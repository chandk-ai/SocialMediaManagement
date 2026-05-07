"""Centralised, type-safe settings using pydantic-settings.

Each config block can be reloaded for tests via Settings(_env_file=...).
Secrets are read from environment; in prod they should originate from the
SecretProvider (AWS Secrets Manager / HashiCorp Vault).
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import AnyUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Supabase pooler helpers ──────────────────────────────────────────────────
def _supabase_project_ref(url: str) -> str | None:
    """Extract `<ref>` from `https://<ref>.supabase.co`."""
    m = re.match(r"^https?://([a-z0-9]+)\.supabase\.co", (url or "").strip(), re.I)
    return m.group(1) if m else None


def _normalize_supabase_db_url(url: str, project_ref: str | None) -> str:
    """Make a Supabase Postgres URL safe for asyncpg + Supavisor.

    * Always uses the async driver: rewrites bare ``postgresql://`` and
      ``postgres://`` to ``postgresql+asyncpg://`` so SQLAlchemy doesn't try to
      load psycopg2 (which we don't ship).
    * Pooler hosts (``*.pooler.supabase.com``) require a tenant-scoped username
      ``postgres.<project_ref>`` — bare ``postgres`` produces
      ``InternalServerError: Tenant or user not found``. Rewrites if needed.
    * No-op for non-postgres URLs.
    """
    if not url:
        return url

    # 1. Force the async driver so we never accidentally hit psycopg2.
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    elif url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]

    # 2. Inject project-ref into pooler username if missing.
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if "pooler.supabase.com" not in (parts.hostname or ""):
        return url
    if not parts.username or parts.username != "postgres":
        return url  # already qualified or unexpected username
    if not project_ref:
        return url
    user = f"postgres.{project_ref}"
    pwd = parts.password
    auth = f"{user}:{pwd}" if pwd is not None else user
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{auth}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def supabase_asyncpg_connect_args(url: str) -> dict[str, Any]:
    """asyncpg connect_args required when talking through Supabase's
    transaction-mode pooler (Supavisor / pgbouncer). Disables the
    prepared-statement cache that breaks under tx pooling."""
    if "pooler.supabase.com" in (url or ""):
        # `statement_cache_size=0` is the well-supported asyncpg switch.
        # We intentionally do NOT set `prepared_statement_cache_size` —
        # older asyncpg builds reject it and crash every request.
        return {"statement_cache_size": 0}
    return {}


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_")
    url: str = "postgresql+asyncpg://smms:smms@postgres:5432/smms"
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")
    url: str = "redis://redis:6379/0"


class QueueSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUEUE_")
    broker_url: str = "amqp://guest:guest@rabbitmq:5672//"
    result_backend: str = "redis://redis:6379/1"


class OktaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OKTA_")
    issuer: AnyUrl | None = None         # e.g. https://dev-xxx.okta.com/oauth2/default
    audience: str = "api://smms"
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    jwks_cache_ttl_sec: int = 3600


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_")
    default_provider: Literal["anthropic", "openai", "azure_openai", "mock"] = "mock"
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    azure_endpoint: str = ""
    azure_api_key: SecretStr = SecretStr("")


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEC_")
    token_vault_master_key: SecretStr = SecretStr("dev-only-replace-me-32bytes-min!!")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    rate_limit_per_minute: int = 120


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OBS_")
    service_name: str = "smms-backend"
    otlp_endpoint: str | None = None     # e.g. http://otel-collector:4317
    log_level: str = "INFO"
    log_json: bool = True


class SupabaseSettings(BaseSettings):
    """Supabase project configuration.

    The pattern we use:
    * SQL traffic (repos)        — direct Postgres via SQLAlchemy/asyncpg using
                                   `postgres_connection_string` (the pooled
                                   `pgbouncer` URL Supabase exposes).
    * Auth / Storage / Realtime  — supabase-py SDK using `url` + the appropriate
                                   anon / service role key.
    """
    model_config = SettingsConfigDict(env_prefix="SUPABASE_")

    url: str = ""                         # https://<project>.supabase.co
    anon_key: SecretStr = SecretStr("")    # safe for client-side
    service_role_key: SecretStr = SecretStr("")  # server-side ONLY, bypasses RLS
    jwt_secret: SecretStr = SecretStr("")  # used to verify Supabase Auth JWTs
    jwt_audience: str = "authenticated"
    storage_bucket: str = "smms-media"
    realtime_channel: str = "smms"
    # Pooled Postgres URL — Supabase shows it under Settings → Database
    # e.g. postgresql+asyncpg://postgres:<pwd>@aws-0-us-east-1.pooler.supabase.com:6543/postgres
    postgres_connection_string: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.url and (self.anon_key.get_secret_value() or self.service_role_key.get_secret_value()))


class Settings(BaseSettings):
    """Aggregate of all settings groups."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    api_prefix: str = "/api/v1"
    api_title: str = "Social Media Management System"

    # `auto` selects supabase if SUPABASE_URL is set, otherwise memory.
    persistence_backend: Literal["auto", "memory", "supabase"] = "auto"
    auth_backend: Literal["auto", "okta", "supabase", "dev"] = "auto"

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    okta: OktaSettings = Field(default_factory=OktaSettings)
    supabase: SupabaseSettings = Field(default_factory=SupabaseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    # ── derived helpers ─────────────────────────────────────────────
    def resolved_persistence_backend(self) -> Literal["memory", "supabase"]:
        if self.persistence_backend != "auto":
            return self.persistence_backend  # type: ignore[return-value]
        return "supabase" if self.supabase.is_configured else "memory"

    def resolved_auth_backend(self) -> Literal["okta", "supabase", "dev"]:
        if self.auth_backend != "auto":
            return self.auth_backend  # type: ignore[return-value]
        if self.supabase.is_configured:
            return "supabase"
        if self.okta.issuer:
            return "okta"
        return "dev"

    def db_url(self) -> str:
        """Postgres URL the repositories connect to.

        Auto-normalizes Supabase pooler URLs so a bare `postgres` username gets
        the required `postgres.<project-ref>` suffix.
        """
        if self.resolved_persistence_backend() == "supabase" and self.supabase.postgres_connection_string:
            return _normalize_supabase_db_url(
                self.supabase.postgres_connection_string,
                _supabase_project_ref(self.supabase.url),
            )
        return self.db.url

    def db_connect_args(self) -> dict[str, Any]:
        """Extra asyncpg connect_args (e.g. statement cache disabled for pooler)."""
        return supabase_asyncpg_connect_args(self.db_url())


@lru_cache
def get_settings() -> Settings:
    return Settings()
