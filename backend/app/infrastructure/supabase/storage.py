"""Supabase Storage adapter — used for media (post images, generated videos).

Why we call the REST endpoint directly via ``httpx`` instead of using
``supabase-py``'s ``client.storage.from_(...).upload(...)`` wrapper:

* The SDK's ``file_options`` keys vary across versions. In 2.x the key
  for content-type is ``"content-type"`` (kebab-case lowercased); in
  earlier 1.x branches it was ``"contentType"``. Passing the wrong
  key silently drops the header, the Storage server then sniffs the
  bytes, defaults to ``text/plain`` for raw binary, and the upload
  fails against a bucket whose ``allowed_mime_types`` doesn't include
  text/plain. We had exactly that incident on May 12 2026 for image
  + video uploads.
* The SDK also doesn't expose a streaming upload API, so a 200 MB
  video would be buffered through the SDK twice. Going to httpx
  ourselves keeps us in control of the request shape.

The bucket is configured via ``SUPABASE_STORAGE_BUCKET`` (default
``smms-media``). For media that platforms fetch server-side (Meta,
Pinterest) we want **permanent public URLs**, not signed/expiring
URLs — see ``public_url()``. Signed URLs are still available via
``signed_url()`` for short-lived in-app previews.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.infrastructure.supabase.client import SupabaseClientFactory

log = get_logger(__name__)


# httpx timeout for large uploads. With a 500 MB ceiling at ~10 MB/s
# that's a worst-case ~50 s; 120 s gives a comfortable margin without
# blocking the worker indefinitely on a hung connection.
_UPLOAD_TIMEOUT = 120.0


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
        """Upload bytes to the bucket at ``path``.

        Returns the storage path on success. Raises an exception on
        Storage rejection (size, mime, auth) — caller surfaces a
        ``MediaImportError`` so the API client sees a clean message
        instead of an httpx stack trace.

        The header we MUST set is ``Content-Type`` — without it the
        Storage server falls back to ``text/plain`` and a strict
        bucket with an ``allowed_mime_types`` list (we have one) will
        return ``415 invalid_mime_type``.
        """
        base = self.settings.supabase.url.rstrip("/")
        # ``service_role_key`` is wrapped in pydantic.SecretStr so it
        # doesn't leak via __repr__ / logging by accident. httpx headers
        # need a plain str — unwrap with .get_secret_value() exactly at
        # the wire boundary, never store the unwrapped form on ``self``.
        service_key_raw = self.settings.supabase.service_role_key
        service_key = (
            service_key_raw.get_secret_value()
            if hasattr(service_key_raw, "get_secret_value")
            else str(service_key_raw)
        )
        url = f"{base}/storage/v1/object/{self.bucket}/{path}"

        ct = content_type or "application/octet-stream"
        headers = {
            "Authorization": f"Bearer {service_key}",
            # The service-role key already grants write — apikey is set
            # for clarity (Supabase accepts either / both).
            "apikey": service_key,
            "Content-Type": ct,
            # ``x-upsert: true`` would let a retry overwrite an existing
            # object at the same key; we don't want that here because
            # every upload path includes a fresh UUID so collisions are
            # vanishingly unlikely and an unexpected overwrite would
            # silently replace another post's media.
            "x-upsert": "false",
        }

        async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
            r = await client.post(url, content=data, headers=headers)
            if r.status_code >= 400:
                # Bubble up the Storage server's JSON error verbatim so
                # the MediaImportError wrapper produces an actionable
                # "size too large" or "mime not allowed" message —
                # don't swallow Supabase's diagnostics into a generic
                # 502.
                try:
                    detail = r.json()
                except Exception:                                     # noqa: BLE001
                    detail = r.text[:500]
                raise RuntimeError(detail)

        return path

    async def signed_url(self, path: str, ttl_seconds: int = 3600) -> str:
        """Returns a time-limited URL that the browser can fetch directly.

        Kept for the in-app preview path (where short-lived URLs are
        fine). External-fetch paths (Meta, Pinterest) must use
        ``public_url`` instead — signed URLs expire mid-publish-cycle
        for scheduled posts.
        """
        client = self.factory.service_role()

        def _do() -> Any:
            return client.storage.from_(self.bucket).create_signed_url(path, ttl_seconds)
        result = await asyncio.to_thread(_do)
        if isinstance(result, dict):
            return result.get("signedURL") or result.get("signed_url") or ""
        return getattr(result, "signed_url", "") or getattr(result, "signedURL", "")

    def public_url(self, path: str) -> str:
        """Returns the permanent public URL for an object. Only valid
        if the bucket is marked ``public=true`` in Supabase (which our
        ``smms-media`` bucket is). Public URLs never expire — that's
        what platforms like Meta need when fetching media for a
        scheduled post that publishes hours or days later."""
        base = self.settings.supabase.url.rstrip("/")
        return f"{base}/storage/v1/object/public/{self.bucket}/{path}"

    async def delete(self, paths: list[str]) -> None:
        client = self.factory.service_role()

        def _do() -> Any:
            return client.storage.from_(self.bucket).remove(paths)
        await asyncio.to_thread(_do)

    async def create_signed_upload_url(self, path: str) -> dict[str, str]:
        """Generate a short-lived signed upload token. The frontend
        calls ``supabase.storage.from(bucket).uploadToSignedUrl(path,
        token, file)`` with what we return — supabase-js then handles
        CORS, content-type negotiation, and (for files > ~6 MB) TUS
        resumable upload chunking. We tried to hand-roll a raw PUT
        against the embedded-token URL but Supabase's Storage server
        doesn't accept the CORS preflight on that endpoint pattern;
        going through supabase-js is the documented and supported way.

        Returns:
          * ``token``        — opaque short-lived (~2 h) credential
                              that authorizes a PUT to ``storage_path``.
          * ``storage_path`` — the key inside the bucket where the file
                              will live after upload.
          * ``bucket``       — the bucket name (so the frontend doesn't
                              hard-code it).
          * ``public_url``   — the permanent URL the post can reference
                              once the upload completes. Matches
                              ``public_url(path)``.
        """
        base = self.settings.supabase.url.rstrip("/")
        service_key_raw = self.settings.supabase.service_role_key
        service_key = (
            service_key_raw.get_secret_value()
            if hasattr(service_key_raw, "get_secret_value")
            else str(service_key_raw)
        )
        url = f"{base}/storage/v1/object/upload/sign/{self.bucket}/{path}"
        headers = {
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, headers=headers)
            if r.status_code >= 400:
                try:
                    detail = r.json()
                except Exception:                                     # noqa: BLE001
                    detail = r.text[:500]
                raise RuntimeError(
                    f"Supabase signed-upload-URL request failed: {detail}"
                )
            data = r.json() or {}

        token = data.get("token") or ""
        if not token:
            # Defensive: older Supabase versions may put the token
            # inside the URL field as ``?token=…``. Extract it so the
            # frontend always gets a plain token regardless of server
            # version.
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(data.get("url") or "").query)
            token = (qs.get("token") or [""])[0]

        return {
            "token": token,
            "storage_path": path,
            "bucket": self.bucket,
            "public_url": self.public_url(path),
        }
