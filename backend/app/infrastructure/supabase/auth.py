"""Verify Supabase Auth JWTs.

Supabase signs user JWTs with HS256 against the project's `JWT secret` (found
under Settings → API). We map the claims onto our `Principal` so the rest of
the API treats Supabase users identically to Okta users.
"""
from __future__ import annotations

from typing import Any

from jose import JWTError, jwt

from app.core.config import Settings, get_settings
from app.domain.entities.user import Role
from app.domain.exceptions import AuthError


class SupabaseAuth:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def verify(self, token: str) -> dict[str, Any]:
        secret = self.settings.supabase.jwt_secret.get_secret_value()
        if not secret:
            raise AuthError("Supabase JWT secret not configured")
        try:
            return jwt.decode(
                token, secret, algorithms=["HS256"],
                audience=self.settings.supabase.jwt_audience,
            )
        except JWTError as exc:
            raise AuthError(f"Supabase token invalid: {exc}") from exc

    @staticmethod
    def map_claims(claims: dict[str, Any]) -> dict[str, Any]:
        """Translate Supabase JWT claims into our Principal fields.

        Convention:
        * Supabase users carry `app_metadata.org_id` and `app_metadata.role`
          (set via Admin API or a database trigger when the user is provisioned).
        * Top-level `sub`, `email` are standard.
        """
        meta = (claims.get("app_metadata") or {}) | (claims.get("user_metadata") or {})
        return {
            "subject": claims["sub"],
            "email": claims.get("email", ""),
            "name": meta.get("display_name") or claims.get("email", ""),
            "org_id": meta.get("org_id", "00000000-0000-0000-0000-000000000001"),
            "role": Role(meta.get("role") or "viewer"),
        }
