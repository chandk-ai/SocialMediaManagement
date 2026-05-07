"""Generic OAuth 2.0 / OIDC helper used by every social-platform adapter.

The flow:
  1. Caller asks for `authorize_url(state=..., redirect_uri=...)` — we build
     the platform's authorize URL with PKCE + a state nonce and return it.
  2. Platform redirects the user back to our callback with `?code=...&state=...`.
  3. Caller calls `exchange_code(code, code_verifier, redirect_uri)` — we POST
     to the token endpoint and return the access token + refresh token.
  4. When the token nears expiry, `refresh(refresh_token)` rotates it.

State + PKCE verifiers are persisted in Redis (so the callback can be served
by any backend pod). Falls back to an in-memory store for dev.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)


# ────────────────────────────────────────────────────────────── helpers
def random_state(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_urlsafe(24)}"


def make_pkce() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge_S256)."""
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ────────────────────────────────────────────── state / PKCE persistence
class StateStore:
    """Redis-backed store. Falls back to a process-local dict if Redis isn't
    configured — fine for dev, but a multi-pod deployment must use Redis."""

    def __init__(self, redis_url: str | None = None, ttl: int = 600) -> None:
        self._ttl = ttl
        self._mem: dict[str, tuple[float, dict]] = {}
        self._redis: Any | None = None
        if redis_url:
            try:
                import redis.asyncio as redis
                self._redis = redis.from_url(redis_url, decode_responses=True)
            except ImportError:
                log.warning("redis_unavailable_using_memory_state_store")

    async def put(self, state: str, payload: dict) -> None:
        if self._redis is not None:
            import json
            await self._redis.setex(f"oauth:{state}", self._ttl, json.dumps(payload))
        else:
            self._mem[state] = (time.time() + self._ttl, payload)

    async def take(self, state: str) -> dict | None:
        if self._redis is not None:
            import json
            raw = await self._redis.getdel(f"oauth:{state}")
            return json.loads(raw) if raw else None
        entry = self._mem.pop(state, None)
        if not entry: return None
        expires, payload = entry
        if expires < time.time(): return None
        return payload


# ────────────────────────────────────────────────── per-provider config
@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    name: str
    authorize_url: str
    token_url: str
    refresh_url: str | None = None
    scopes: tuple[str, ...] = ()
    use_pkce: bool = True
    client_auth_in_body: bool = True   # vs. Basic auth header
    extra_authorize_params: dict[str, str] | None = None
    extra_token_params: dict[str, str] | None = None


# Meta Graph API version used for Facebook / Instagram / Threads.
# Bumping versions is a coordinated change because deprecated versions get sunset.
_META_API_VERSION = "v21.0"

# Curated config for the platforms we ship adapters for. Add more by importing
# this module and registering — keeps the social-platform adapter files thin.
PROVIDERS: dict[str, OAuthProviderConfig] = {
    "linkedin": OAuthProviderConfig(
        name="linkedin",
        authorize_url="https://www.linkedin.com/oauth/v2/authorization",
        token_url="https://www.linkedin.com/oauth/v2/accessToken",
        scopes=("openid", "profile", "email", "w_member_social"),
        use_pkce=False,            # LinkedIn doesn't support PKCE on web app type
    ),
    "twitter": OAuthProviderConfig(
        # X kept the twitter.com OAuth host for compatibility. Both work; we
        # use x.com because that's what X's developer docs publish today.
        name="twitter",
        authorize_url="https://x.com/i/oauth2/authorize",
        token_url="https://api.x.com/2/oauth2/token",
        refresh_url="https://api.x.com/2/oauth2/token",
        scopes=("tweet.read", "tweet.write", "users.read", "offline.access"),
        use_pkce=True,
        client_auth_in_body=False,  # X requires Basic auth header
    ),
    "facebook": OAuthProviderConfig(
        name="facebook",
        authorize_url=f"https://www.facebook.com/{_META_API_VERSION}/dialog/oauth",
        token_url=f"https://graph.facebook.com/{_META_API_VERSION}/oauth/access_token",
        scopes=("pages_manage_posts", "pages_read_engagement",
                "pages_show_list", "business_management"),
        use_pkce=False,
    ),
    "instagram": OAuthProviderConfig(
        # NOTE: posting to Instagram requires an IG *Business* or *Creator*
        # account that is linked to a Facebook Page. The OAuth flow therefore
        # goes through Meta (facebook.com) — this is by Meta's design, not a
        # bug in our code. The user lands on a Facebook consent screen that
        # lists "<App> would like to access your Instagram Business account".
        # Scopes are exactly what Meta's "API setup with Facebook login"
        # wizard surfaces under "Manage content on Instagram".
        name="instagram",
        authorize_url=f"https://www.facebook.com/{_META_API_VERSION}/dialog/oauth",
        token_url=f"https://graph.facebook.com/{_META_API_VERSION}/oauth/access_token",
        scopes=("instagram_basic", "instagram_content_publish",
                "pages_show_list", "pages_read_engagement",
                "business_management"),
        use_pkce=False,
    ),
    "threads": OAuthProviderConfig(
        # Threads has its OWN OAuth (independent of Facebook) since 2024.
        name="threads",
        authorize_url="https://threads.net/oauth/authorize",
        token_url="https://graph.threads.net/oauth/access_token",
        refresh_url="https://graph.threads.net/refresh_access_token",
        scopes=("threads_basic", "threads_content_publish"),
        use_pkce=False,
    ),
    "youtube": OAuthProviderConfig(
        name="youtube",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        refresh_url="https://oauth2.googleapis.com/token",
        scopes=(
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ),
        use_pkce=True,
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
    ),
    "tiktok": OAuthProviderConfig(
        name="tiktok",
        authorize_url="https://www.tiktok.com/v2/auth/authorize/",
        token_url="https://open.tiktokapis.com/v2/oauth/token/",
        refresh_url="https://open.tiktokapis.com/v2/oauth/token/",
        scopes=("user.info.basic", "video.upload", "video.publish"),
        use_pkce=True,
    ),
    "reddit": OAuthProviderConfig(
        name="reddit",
        authorize_url="https://www.reddit.com/api/v1/authorize",
        token_url="https://www.reddit.com/api/v1/access_token",
        refresh_url="https://www.reddit.com/api/v1/access_token",
        scopes=("submit", "identity", "read"),
        use_pkce=False,
        client_auth_in_body=False,
        extra_authorize_params={"duration": "permanent"},  # required for refresh tokens
    ),
    "pinterest": OAuthProviderConfig(
        name="pinterest",
        authorize_url="https://www.pinterest.com/oauth/",
        token_url="https://api.pinterest.com/v5/oauth/token",
        refresh_url="https://api.pinterest.com/v5/oauth/token",
        scopes=("boards:read", "pins:read", "pins:write"),
        use_pkce=False,
        client_auth_in_body=False,
    ),
    "discord": OAuthProviderConfig(
        name="discord",
        authorize_url="https://discord.com/api/oauth2/authorize",
        token_url="https://discord.com/api/oauth2/token",
        refresh_url="https://discord.com/api/oauth2/token",
        scopes=("bot", "applications.commands"),
        use_pkce=False,
        client_auth_in_body=True,
    ),
    "slack": OAuthProviderConfig(
        name="slack",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        scopes=("chat:write", "channels:read", "groups:read"),
        use_pkce=False,
        client_auth_in_body=True,
    ),
}


# Some providers share a single OAuth app — e.g. one Meta app handles
# Facebook + Instagram + Threads, one Google Cloud app handles YouTube.
# Map: plugin_name → "shared family" name. When env vars for the plugin
# itself are missing, we fall back to the family name.
_CREDENTIAL_FAMILY: dict[str, str] = {
    "facebook":  "meta",
    "instagram": "meta",
    "threads":   "meta",
    "youtube":   "google",
}


# ────────────────────────────────────────────────────────── client
@dataclass(frozen=True, slots=True)
class TokenResponse:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scope: str | None
    raw: dict[str, Any]


class OAuthClient:
    """Stateless OAuth runner — per-platform configuration drives behaviour."""

    def __init__(
        self, provider: OAuthProviderConfig,
        client_id: str, client_secret: str,
        state_store: StateStore | None = None,
    ) -> None:
        self.provider = provider
        self.client_id = client_id
        self.client_secret = client_secret
        self.state_store = state_store or StateStore()

    async def authorize_url(
        self, *, redirect_uri: str, extra_state: dict | None = None,
        scopes: tuple[str, ...] | None = None,
    ) -> str:
        state = random_state(self.provider.name + ":")
        payload: dict[str, Any] = dict(extra_state or {})
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes or self.provider.scopes),
            "state": state,
        }
        if self.provider.use_pkce:
            verifier, challenge = make_pkce()
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"
            payload["code_verifier"] = verifier
        if self.provider.extra_authorize_params:
            params.update(self.provider.extra_authorize_params)
        payload["redirect_uri"] = redirect_uri
        await self.state_store.put(state, payload)
        return f"{self.provider.authorize_url}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, state: str) -> TokenResponse:
        saved = await self.state_store.take(state)
        if saved is None:
            raise OAuthError("state not found or expired")
        body: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": saved["redirect_uri"],
        }
        if self.provider.use_pkce and (verifier := saved.get("code_verifier")):
            body["code_verifier"] = verifier
        return await self._token_call(body)

    async def refresh(self, refresh_token: str) -> TokenResponse:
        body = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        return await self._token_call(body, url=self.provider.refresh_url)

    async def _token_call(
        self, body: dict[str, str], *, url: str | None = None,
    ) -> TokenResponse:
        target = url or self.provider.token_url
        headers = {"Accept": "application/json"}
        if self.provider.client_auth_in_body:
            body = {**body, "client_id": self.client_id,
                    "client_secret": self.client_secret}
        else:
            basic = base64.b64encode(
                f"{self.client_id}:{self.client_secret}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {basic}"
        if self.provider.extra_token_params:
            body = {**body, **self.provider.extra_token_params}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(target, data=body, headers=headers)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as exc:
            raise OAuthError(f"{self.provider.name} token call failed: {exc}") from exc
        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
            scope=data.get("scope"),
            raw=data,
        )


class OAuthError(Exception):
    pass


# Process-singleton state store shared by every OAuthClient. Without this,
# the state nonce stored during `authorize_url` is invisible to the request
# that handles the callback — every request would otherwise spin up its own
# in-memory store and "state not found or expired" would always fire.
_GLOBAL_STATE_STORE: StateStore | None = None


def _get_state_store() -> StateStore:
    global _GLOBAL_STATE_STORE
    if _GLOBAL_STATE_STORE is None:
        _GLOBAL_STATE_STORE = StateStore(redis_url=os.getenv("REDIS_URL"))
    return _GLOBAL_STATE_STORE


def _resolve_credentials(plugin_name: str) -> tuple[str, str]:
    """Look up client_id / client_secret for a plugin.

    Order:
      1. ``<PLUGIN>_CLIENT_ID`` / ``<PLUGIN>_CLIENT_SECRET`` (per-plugin)
      2. Shared family fallback for plugins that share a Meta/Google app
         (``META_CLIENT_ID`` covers facebook + instagram + threads;
         ``GOOGLE_CLIENT_ID`` covers youtube).
    """
    cid = os.getenv(f"{plugin_name.upper()}_CLIENT_ID", "")
    cs  = os.getenv(f"{plugin_name.upper()}_CLIENT_SECRET", "")
    if cid and cs:
        return cid, cs
    family = _CREDENTIAL_FAMILY.get(plugin_name)
    if family:
        cid = cid or os.getenv(f"{family.upper()}_CLIENT_ID", "")
        cs  = cs  or os.getenv(f"{family.upper()}_CLIENT_SECRET", "")
    return cid, cs


# Convenience factory used by the API layer.
def get_oauth_client(plugin_name: str) -> OAuthClient:
    """Build a client from environment-supplied client_id/secret."""
    provider = PROVIDERS.get(plugin_name)
    if provider is None:
        raise OAuthError(f"no OAuth provider registered for {plugin_name!r}")
    cid, cs = _resolve_credentials(plugin_name)
    if not cid or not cs:
        family = _CREDENTIAL_FAMILY.get(plugin_name)
        hint = (
            f" Set {plugin_name.upper()}_CLIENT_ID + {plugin_name.upper()}_CLIENT_SECRET"
            + (f" (or the shared {family.upper()}_CLIENT_ID/{family.upper()}_CLIENT_SECRET)" if family else "")
            + " on the backend service."
        )
        raise OAuthError(f"OAuth credentials not configured for {plugin_name!r}.{hint}")
    return OAuthClient(provider, client_id=cid, client_secret=cs,
                       state_store=_get_state_store())
