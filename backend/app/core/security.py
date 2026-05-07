"""OIDC / OAuth2 verification against Okta (or any OIDC provider).

The frontend (NextAuth + Okta) sends the ID/Access token as a Bearer header.
Backend verifies it against the issuer's JWKS, then maps the claims onto our
domain `User` model.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import Settings, get_settings
from app.domain.entities.user import Role
from app.domain.exceptions import AuthError

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    """Decoded, verified caller identity."""
    subject: str
    email: str
    name: str
    org_id: str
    role: Role
    raw_claims: dict[str, Any]


class JWKSCache:
    """Tiny TTL cache for the JWKS document — avoids re-fetching every call."""

    def __init__(self, jwks_uri: str, ttl: int) -> None:
        self.jwks_uri = jwks_uri
        self.ttl = ttl
        self._keys: dict[str, Any] | None = None
        self._loaded_at: float = 0.0

    async def get(self) -> dict[str, Any]:
        if self._keys is None or time.time() - self._loaded_at > self.ttl:
            return await self._refresh()
        return self._keys

    async def _refresh(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.jwks_uri)
            resp.raise_for_status()
            self._keys = resp.json()
            self._loaded_at = time.time()
            return self._keys


_jwks_cache: JWKSCache | None = None


def _get_jwks_cache(settings: Settings) -> JWKSCache:
    global _jwks_cache
    if _jwks_cache is None:
        if not settings.okta.issuer:
            raise AuthError("Okta issuer not configured")
        jwks_uri = f"{str(settings.okta.issuer).rstrip('/')}/v1/keys"
        _jwks_cache = JWKSCache(jwks_uri=jwks_uri, ttl=settings.okta.jwks_cache_ttl_sec)
    return _jwks_cache


async def verify_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify a JWT against Okta JWKS and return the claims."""
    cache = _get_jwks_cache(settings)
    jwks = await cache.get()
    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if key is None:
            raise AuthError("Signing key not found")
        claims = jwt.decode(
            token, key,
            algorithms=[key.get("alg", "RS256")],
            audience=settings.okta.audience,
            issuer=str(settings.okta.issuer).rstrip("/"),
        )
        return claims
    except JWTError as exc:
        raise AuthError(f"Token verification failed: {exc}") from exc


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    # Dev / test mode shortcut: accept signed local JWT or a static dev token.
    if settings.env in {"dev", "test"} and creds.credentials.startswith("dev."):
        return _dev_principal(creds.credentials)

    backend = settings.resolved_auth_backend()
    try:
        if backend == "supabase":
            from app.infrastructure.supabase.auth import SupabaseAuth
            sa = SupabaseAuth(settings)
            raw = await sa.verify(creds.credentials)
            mapped = SupabaseAuth.map_claims(raw)
            return Principal(
                subject=mapped["subject"], email=mapped["email"], name=mapped["name"],
                org_id=mapped["org_id"], role=mapped["role"], raw_claims=raw,
            )
        # default: Okta / OIDC
        claims = await verify_token(creds.credentials, settings)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    role_claim = claims.get("role") or "viewer"
    return Principal(
        subject=claims["sub"],
        email=claims.get("email", ""),
        name=claims.get("name", ""),
        org_id=claims.get("org_id") or claims.get("https://smms/org_id", ""),
        role=Role(role_claim),
        raw_claims=claims,
    )


def requires_role(*allowed: Role):
    """FastAPI dependency factory — enforce one of the given roles."""
    async def _dep(user: Principal = Depends(get_current_user)) -> Principal:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user
    return _dep


def _dev_principal(token: str) -> Principal:
    """Parse a dev token of the form 'dev.<role>.<email>.<org_id>'."""
    parts = token.split(".")
    role = Role(parts[1]) if len(parts) > 1 else Role.ADMIN
    email = parts[2] if len(parts) > 2 else "dev@example.com"
    org_id = parts[3] if len(parts) > 3 else "00000000-0000-0000-0000-000000000001"
    return Principal(
        subject="dev-user",
        email=email,
        name="Dev User",
        org_id=org_id,
        role=role,
        raw_claims={"dev": True},
    )
