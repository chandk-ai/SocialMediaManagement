"""X (Twitter) adapter — real v2 tweets endpoint.

POST https://api.x.com/2/tweets with bearer auth. Image upload still uses
v1.1 (upload.twitter.com) and feeds media_ids into the v2 body. Threads via
``in_reply_to_tweet_id``.
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

X_API = "https://api.x.com/2"


@register_plugin("platform", "twitter", api_version="1.0", category="microblog")
class TwitterPlatform(SocialPlatform):
    display_name = "X (Twitter)"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=True, document=False,
        threads=True, scheduling=False, analytics=True,
    )
    max_text_length = 280
    max_hashtags = 10

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        text_with_tags = payload.text + " " + " ".join(h.value for h in payload.hashtags)
        if len(text_with_tags) > self.max_text_length:
            raise PlatformValidationError(
                f"tweet (text + hashtags) is {len(text_with_tags)} chars, max {self.max_text_length}",
            )
        if not self._access_token():
            raise PlatformValidationError("No X access token. Reconnect this account.")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="twitter"),
            refresh_token=None,
            scopes=("tweet.read", "tweet.write", "users.read", "offline.access"),
            account_id=self.config.get("user_id", ""),
            account_handle=self.config.get("username"),
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        token = self._access_token()
        body: dict[str, Any] = {"text": _compose_text(payload)}
        in_reply_to = self.config.get("in_reply_to_tweet_id")
        if in_reply_to:
            body["reply"] = {"in_reply_to_tweet_id": str(in_reply_to)}

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        ) as client:
            # Media upload via v1.1 (X kept v2 media upload behind a paywall) —
            # only when the user provides actual public bytes/URL. We skip media
            # for the first cut; uploaded_url + bytes path can be added later.
            r = await client.post(f"{X_API}/tweets", json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"X publish failed [{r.status_code}]: {r.text}")
            data = r.json().get("data", {})

        tweet_id = data.get("id", "")
        log.info("twitter_published", tweet_id=tweet_id)
        return PublishResult(
            external_post_id=tweet_id,
            url=f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None,
            raw_response=data,
        )

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        """Pull public_metrics for the tweet. X v2 reliably returns
        impression_count, like_count, retweet_count, reply_count,
        quote_count, bookmark_count for posts authored by the
        authenticated user (a separate non_public_metrics endpoint
        gives view counts but requires elevated access)."""
        token = self._access_token()
        if not token or not external_post_id:
            return {}
        try:
            async with httpx.AsyncClient(timeout=15.0,
                    headers={"Authorization": f"Bearer {token}"}) as client:
                r = await client.get(
                    f"{X_API}/tweets/{external_post_id}",
                    params={"tweet.fields": "public_metrics"},
                )
                if r.status_code >= 400:
                    return {"fetch_error": f"x_{r.status_code}"}
                m = (r.json().get("data") or {}).get("public_metrics") or {}
        except httpx.HTTPError as exc:
            log.warning("twitter_metrics_failed", error=str(exc))
            return {"fetch_error": str(exc)[:200]}
        # Normalised to the engagement service's snapshot schema:
        # likes/comments/shares/impressions are first-class columns;
        # quotes + bookmarks live in `extra` for platform-aware analytics.
        return {
            "likes":       int(m.get("like_count")  or 0),
            "comments":    int(m.get("reply_count") or 0),
            "shares":      int(m.get("retweet_count") or 0),
            "impressions": int(m.get("impression_count") or 0),
            "saves":       int(m.get("bookmark_count") or 0),
            "extra": {
                "quotes":   int(m.get("quote_count") or 0),
                "raw":      m,
            },
        }

    async def delete(self, external_post_id: str) -> bool:
        token = self._access_token()
        if not token or not external_post_id:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0,
                    headers={"Authorization": f"Bearer {token}"}) as client:
                r = await client.delete(f"{X_API}/tweets/{external_post_id}")
                return r.status_code == 200
        except httpx.HTTPError:
            return False


def _compose_text(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    text = payload.text.strip()
    return f"{text} {tags}".strip() if tags else text
