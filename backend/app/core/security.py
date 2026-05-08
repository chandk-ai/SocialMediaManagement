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
from app.domain.exceptions import AuthError, NoMembershipError
from app.infrastructure.observability.sentry import set_request_context

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
        principal = _dev_principal(creds.credentials)
        _tag_principal_in_sentry(principal)
        return principal

    backend = settings.resolved_auth_backend()
    try:
        if backend == "supabase":
            from app.infrastructure.supabase.auth import SupabaseAuth
            sa = SupabaseAuth(settings)
            raw = await sa.verify(creds.credentials)
            mapped = SupabaseAuth.map_claims(raw)
            # Email-claim auto-join — when the JWT doesn't carry an org_id
            # in app_metadata, resolve via the local membership table or
            # a pending invitation. Bootstraps the very first user as
            # admin; refuses subsequent unclaimed signups (NoMembershipError
            # → 403). See app/services/team.py for the full resolution.
            mapped = await _maybe_resolve_via_team(mapped)
            principal = Principal(
                subject=mapped["subject"], email=mapped["email"], name=mapped["name"],
                org_id=mapped["org_id"], role=mapped["role"], raw_claims=raw,
            )
            _tag_principal_in_sentry(principal)
            return principal
        # default: Okta / OIDC
        claims = await verify_token(creds.credentials, settings)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except NoMembershipError as exc:
        # Authenticated, but no workspace access yet — surface a clean 403
        # with a code the frontend can branch on.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "no_membership", "message": str(exc)},
        ) from exc

    role_claim = claims.get("role") or "viewer"
    principal = Principal(
        subject=claims["sub"],
        email=claims.get("email", ""),
        name=claims.get("name", ""),
        org_id=claims.get("org_id") or claims.get("https://smms/org_id", ""),
        role=Role(role_claim),
        raw_claims=claims,
    )
    _tag_principal_in_sentry(principal)
    return principal


async def _maybe_resolve_via_team(mapped: dict[str, Any]) -> dict[str, Any]:
    """Translate a verified Supabase JWT into a fully-populated mapped-claims
    dict, attaching the org_id + role from our local membership table.

    Resolution order:
      1. ``app_metadata.org_id`` already present (provisioned via Supabase
         admin) → use as-is.
      2. ``team.resolve_principal()`` matches a local user row, a pending
         invitation, or backfills ``supabase_uid`` on a pre-provisioned row.
      3. Bootstrap path: if no org exists yet in the deployment, the
         caller becomes the admin of a freshly-created org (one-time).
      4. None of the above → raise :class:`NoMembershipError`. The auth
         dependency turns this into a 403 with a clear message; the
         frontend renders an "ask your admin to invite you" screen.

    The previous implementation silently bucketed unclaimed users into a
    shared placeholder org — a multi-tenant foot-gun. Removed.
    """
    if mapped.get("org_id"):
        return mapped

    # In-memory mode (no Supabase / Postgres backend) — the team service
    # isn't available. Allow the request through with a synthetic dev org
    # so dev / tests keep working without a database.
    try:
        from app.api.deps import get_team_service
        team = get_team_service()
    except Exception:                                                   # noqa: BLE001
        team = None
    if team is None:
        return {**mapped, "org_id": "00000000-0000-0000-0000-000000000001"}

    try:
        resolved = await team.resolve_principal(
            supabase_uid=mapped["subject"],
            email=mapped.get("email", ""),
            display_name=mapped.get("name", ""),
        )
    except Exception as exc:                                            # noqa: BLE001
        # Genuine DB outage — fail open is worse than failing closed for
        # auth, so surface the error rather than guessing an org.
        raise AuthError(f"membership lookup failed: {exc}") from exc

    if resolved is None:
        # Try the bootstrap path: very first user on a fresh deployment
        # becomes admin of a freshly-created org. After that, unclaimed
        # signups are refused — the system is invitation-based.
        try:
            resolved = await team.bootstrap_first_user(
                supabase_uid=mapped["subject"],
                email=mapped.get("email", ""),
                display_name=mapped.get("name", ""),
            )
        except Exception as exc:                                        # noqa: BLE001
            raise AuthError(f"bootstrap failed: {exc}") from exc

    if resolved is None:
        from app.domain.exceptions import NoMembershipError
        raise NoMembershipError(
            "Your sign-in worked, but you don't have access to any "
            "workspace yet. Ask an admin in your organisation to invite "
            f"{mapped.get('email') or 'your email'} via Settings → Team.",
        )

    return {
        **mapped,
        "org_id": resolved.org_id,
        "role": resolved.role,
        "name": resolved.display_name or mapped.get("name", ""),
        # subject (Supabase UID) stays as-is — that's the auth identity.
    }


def _tag_principal_in_sentry(principal: Principal) -> None:
    """Push user_id / org_id / role onto the current Sentry scope so any
    error raised after auth resolves carries the affected tenant. No-op when
    Sentry isn't initialised."""
    try:
        set_request_context(
            user_id=principal.subject or None,
            org_id=principal.org_id or None,
            role=principal.role.value if hasattr(principal.role, "value") else str(principal.role),
        )
    except Exception:                                                   # noqa: BLE001
        pass


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
