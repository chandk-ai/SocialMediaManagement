"""Reddit adapter — real publish via /api/submit.

OAuth-based. After OAuth completes, we use the bearer token. Submitting a
post requires choosing a subreddit; that's stored in ``config.subreddit``.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.domain.value_objects.credentials import EncryptedToken, OAuthCredentials
from app.plugins.registry import register_plugin

from .base import (
    PlatformCapabilities,
    PlatformValidationError,
    PostPayload,
    PublishResult,
    SocialPlatform,
)

log = get_logger(__name__)

REDDIT_API = "https://oauth.reddit.com"


@register_plugin("platform", "reddit", api_version="1.0", category="forum")
class RedditPlatform(SocialPlatform):
    display_name = "Reddit"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=False, document=False,
        threads=False, scheduling=False, analytics=False,
    )
    max_text_length = 40000
    max_hashtags = 0          # Reddit doesn't really use hashtags

    config_schema = {
        "type": "object",
        "required": ["subreddit"],
        "properties": {
            "subreddit": {
                "type": "string",
                "title": "Subreddit",
                "description": "Without /r/ prefix — e.g. 'nonprofits' or 'pythonprogramming'",
            },
            "user_agent": {
                "type": "string",
                "title": "User-Agent (recommended)",
                "default": "smms/1.0 (by /u/yourname)",
                "description": "Reddit blocks generic UAs. Set this to your app + contact handle.",
            },
        },
    }

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self.config.get("subreddit"):
            raise PlatformValidationError("subreddit is required")
        if not self._access_token():
            raise PlatformValidationError("No Reddit access token. Reconnect.")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="reddit"),
            refresh_token=None,
            scopes=("submit", "identity"),
            account_id=self.config.get("subreddit", ""),
            account_handle=f"r/{self.config.get('subreddit', '')}",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        token = self._access_token()
        subreddit = self.config["subreddit"].lstrip("/r/")

        # Self-post (text). Title from payload.extra or first 100 chars of text.
        title = (payload.extra.get("title") or payload.text[:100] or "Untitled").strip()
        text_body = _compose(payload)

        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": self.config.get("user_agent") or "smms/1.0",
        }
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            r = await client.post(
                f"{REDDIT_API}/api/submit",
                data={
                    "sr": subreddit,
                    "kind": "self",
                    "title": title[:300],          # reddit max
                    "text": text_body,
                    "api_type": "json",
                    "sendreplies": "false",
                },
            )
            if r.status_code >= 400:
                raise RuntimeError(f"Reddit submit failed [{r.status_code}]: {r.text}")
            data = r.json()
            j = (data.get("json") or {})
            errors = j.get("errors") or []
            if errors:
                raise RuntimeError(f"Reddit errors: {errors}")
            d = j.get("data") or {}

        post_url = d.get("url", "")
        post_id = d.get("name", "") or d.get("id", "")
        log.info("reddit_published", subreddit=subreddit, id=post_id)
        return PublishResult(
            external_post_id=post_id,
            url=post_url or None,
            raw_response=data,
        )


def _compose(payload: PostPayload) -> str:
    return payload.text.strip()
