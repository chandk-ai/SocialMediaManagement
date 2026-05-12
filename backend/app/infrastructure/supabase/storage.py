"""Supabase Storage adapter — used for media (post images, generated videos).

The storage bucket is configured via `SUPABASE_STORAGE_BUCKET` (default
`smms-media`). The adapter uses signed URLs so the frontend can render media
without exposing tokens.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.infrastructure.supabase.client import SupabaseClientFactory

log = get_logger(__name__)


class SupabaseStorage:
    def __init__(
        self,
        factory: SupabaseClientFactory | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.factory = factory or SupabaseClientFactory(self.settings)
        self.bucket = self.settings.supabase.storage_bucket

    async def upload(self, path: str, data: bytes, content_type: str | None = None) -> str:
        """Upload bytes; returns the storage path (use `signed_url` to share)."""
        client = self.factory.service_role()

        def _do() -> Any:
            return client.storage.from_(self.bucket).upload(
                path, data, {"contentType": content_type} if content_type else None,
            )
        await asyncio.to_thread(_do)
        return path

    async def signed_url(self, path: str, ttl_seconds: int = 3600) -> str:
        """Returns a time-limited URL that the browser can fetch directly."""
        client = self.factory.service_role()

        def _do() -> Any:
            return client.storage.from_(self.bucket).create_signed_url(path, ttl_seconds)
        result = await asyncio.to_thread(_do)
        # supabase-py wraps the response shape — handle both
        if isinstance(result, dict):
            return result.get("signedURL") or result.get("signed_url") or ""
        return getattr(result, "signed_url", "") or getattr(result, "signedURL", "")

    def public_url(self, path: str) -> str:
        """Returns the permanent public URL for an object. Only valid if
        the bucket is marked ``public=true`` in Supabase (which our
        ``smms-media`` bucket is) — otherwise the URL 404s for
        unauthenticated readers.

        We need this — not signed_url — for media that platforms like
        Meta fetch server-side at unpredictable times (e.g. scheduled
        posts that publish hours or days later). Signed URLs would
        expire before Meta's fetch happened. Public URLs never expire.
        """
        base = self.settings.supabase.url.rstrip("/")
        return f"{base}/storage/v1/object/public/{self.bucket}/{path}"

    async def delete(self, paths: list[str]) -> None:
        client = self.factory.service_role()

        def _do() -> Any:
            return client.storage.from_(self.bucket).remove(paths)
        await asyncio.to_thread(_do)
