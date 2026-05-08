"""Verify Supabase Auth JWTs.

Supabase has migrated newer projects to **asymmetric** signing (ES256 / RS256)
served via a JWKS endpoint at `{project}.supabase.co/auth/v1/.well-known/jwks.json`.
Older projects (and the "legacy" HS256 secret) still use a shared HMAC secret.

This verifier supports both:
  * Inspect the unverified JWT header → read `alg` and `kid`.
  * `HS*`  → verify with `supabase.jwt_secret` (legacy / shared secret).
  * `RS*` / `ES*` / `EdDSA` → fetch JWKS, find matching `kid`, verify with
    the corresponding public key. JWKS is cached in-process with a TTL.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from jose import JWTError, jwt

from app.core.config import Settings, get_settings
from app.domain.entities.user import Role
from app.domain.exceptions import AuthError

_JWKS_TTL_SECONDS = 3600  # cache JWKS for an hour
_HTTP_TIMEOUT = 5.0


class _JwksCache:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._url: str | None = None
        self._fetched_at: float = 0.0
        self._keys: list[dict[str, Any]] = []

    async def get(self, jwks_url: str, *, force: bool = False) -> list[dict[str, Any]]:
        now = time.time()
        if (
            not force
            and self._url == jwks_url
            and self._keys
            and (now - self._fetched_at) < _JWKS_TTL_SECONDS
        ):
            return self._keys
        async with self._lock:
            # Double-check under lock
            now = time.time()
            if (
                not force
                and self._url == jwks_url
                and self._keys
                and (now - self._fetched_at) < _JWKS_TTL_SECONDS
            ):
                return self._keys
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(jwks_url)
                resp.raise_for_status()
                payload = resp.json()
            keys = payload.get("keys") or []
            if not isinstance(keys, list) or not keys:
                raise AuthError(f"JWKS at {jwks_url} returned no keys")
            self._url = jwks_url
            self._keys = keys
            self._fetched_at = now
            return keys


_jwks_cache = _JwksCache()


def _jwks_url_for(settings: Settings) -> str:
    base = (settings.supabase.url or "").rstrip("/")
    if not base:
        raise AuthError("SUPABASE_URL not configured — cannot fetch JWKS")
    return f"{base}/auth/v1/.well-known/jwks.json"


def _select_key(keys: list[dict[str, Any]], kid: str | None) -> dict[str, Any]:
    if kid:
        for k in keys:
            if k.get("kid") == kid:
                return k
    # Fallback: a single key with no kid match
    if len(keys) == 1:
        return keys[0]
    raise AuthError(f"No JWKS key matched kid={kid!r}")


class SupabaseAuth:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise AuthError(f"Supabase token invalid header: {exc}") from exc

        alg = (header.get("alg") or "").upper()
        audience = self.settings.supabase.jwt_audience

        # ---- Symmetric (legacy / HS*) -----------------------------------------
        if alg.startswith("HS"):
            secret = self.settings.supabase.jwt_secret.get_secret_value()
            if not secret:
                raise AuthError(
                    "Supabase JWT secret not configured — set SUPABASE_JWT_SECRET "
                    "(legacy HS256 secret) or rely on JWKS for asymmetric tokens.",
                )
            try:
                return jwt.decode(token, secret, algorithms=[alg], audience=audience)
            except JWTError as exc:
                raise AuthError(f"Supabase token invalid: {exc}") from exc

        # ---- Asymmetric (RS* / ES* / EdDSA) -----------------------------------
        if alg.startswith(("RS", "ES")) or alg in {"EDDSA", "PS256", "PS384", "PS512"}:
            jwks_url = _jwks_url_for(self.settings)
            try:
                keys = await _jwks_cache.get(jwks_url)
                key_dict = _select_key(keys, header.get("kid"))
            except (httpx.HTTPError, AuthError) as exc:
                raise AuthError(f"Could not load Supabase JWKS: {exc}") from exc

            try:
                # python-jose accepts a JWK dict directly as the key
                return jwt.decode(token, key_dict, algorithms=[alg], audience=audience)
            except JWTError:
                # kid may have rotated — force-refresh once and retry
                try:
                    keys = await _jwks_cache.get(jwks_url, force=True)
                    key_dict = _select_key(keys, header.get("kid"))
                    return jwt.decode(token, key_dict, algorithms=[alg], audience=audience)
                except JWTError as exc2:
                    raise AuthError(f"Supabase token invalid: {exc2}") from exc2

        raise AuthError(f"Supabase token uses unsupported alg: {alg}")

    @staticmethod
    def map_claims(claims: dict[str, Any]) -> dict[str, Any]:
        """Translate Supabase JWT claims into our Principal fields.

        Convention:
        * Supabase users CAN carry ``app_metadata.org_id`` + ``app_metadata.role``
          (set via Admin API or a database trigger when provisioned). When
          present, those win and the auth path uses them directly.
        * When absent (the common case for fresh sign-ins), ``org_id`` is
          left as ``None`` so :func:`_maybe_resolve_via_team` can resolve
          via the local users table or pending invitation. The previous
          implementation defaulted to a shared placeholder org which was
          a multi-tenant foot-gun — every unclaimed signup landed in the
          same workspace and could see each other's data.
        * Top-level `sub`, `email` are standard.
        """
        meta = (claims.get("app_metadata") or {}) | (claims.get("user_metadata") or {})
        return {
            "subject": claims["sub"],
            "email": claims.get("email", ""),
            "name": meta.get("display_name") or claims.get("email", ""),
            "org_id": meta.get("org_id"),       # None → resolve via team service
            "role": Role(meta.get("role") or "viewer"),
        }
