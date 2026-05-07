"""Thin wrapper around supabase-py with two-tier client policy.

* `service_role` client  — used by trusted backend code; bypasses RLS.
* `user` client          — instantiated per request, JWT-scoped, RLS-enforced.

Falls back to a mock client when supabase-py is not installed so the rest of
the stack still imports.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class _MockTable:
    def __init__(self, name: str): self.name = name
    def select(self, *_a, **_kw): return self
    def insert(self, *_a, **_kw): return self
    def update(self, *_a, **_kw): return self
    def delete(self, *_a, **_kw): return self
    def eq(self, *_a, **_kw): return self
    def execute(self): return type("R", (), {"data": [], "count": 0})()


class _MockClient:
    """Stand-in used when supabase-py isn't installed (CI, offline dev)."""
    def __init__(self, url: str, key: str) -> None:
        self.url, self.key = url, key
    def table(self, name: str) -> _MockTable: return _MockTable(name)
    def from_(self, name: str) -> _MockTable: return _MockTable(name)
    @property
    def auth(self): return self
    @property
    def storage(self): return self


class SupabaseClientFactory:
    """Builds clients on demand.

    Note: supabase-py is sync; we wrap it in `asyncio.to_thread` from callers
    that need async. The DB-heavy paths bypass this entirely and use
    SQLAlchemy/asyncpg directly against Supabase Postgres.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def service_role(self) -> Any:
        """Server-trusted client — bypasses RLS."""
        return self._build(
            self.settings.supabase.service_role_key.get_secret_value()
            or self.settings.supabase.anon_key.get_secret_value()
        )

    def for_user(self, user_jwt: str) -> Any:
        """Per-request client — RLS enforced as the authenticated user."""
        client = self._build(self.settings.supabase.anon_key.get_secret_value())
        # When supabase-py is present, attach the user JWT so PostgREST/Realtime
        # see this caller as the authenticated user.
        try:
            client.postgrest.auth(user_jwt)
        except Exception:                          # noqa: BLE001
            pass
        return client

    def _build(self, key: str) -> Any:
        url = self.settings.supabase.url
        if not url or not key:
            log.info("supabase_not_configured_using_mock_client")
            return _MockClient(url, key)
        try:
            from supabase import create_client
        except ImportError:
            log.info("supabase_sdk_missing_using_mock_client")
            return _MockClient(url, key)
        return create_client(url, key)


@lru_cache
def get_supabase_factory() -> SupabaseClientFactory:
    return SupabaseClientFactory()


def get_supabase_client():
    """Convenience: service-role client. Most repos use this."""
    return get_supabase_factory().service_role()
