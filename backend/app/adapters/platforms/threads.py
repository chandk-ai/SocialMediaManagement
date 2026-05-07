"""Meta Threads adapter — real Threads Graph API publishing.

Threads has its OWN API (separate from Facebook/Instagram), reachable at
``graph.threads.net``. Like Instagram, publishing is two steps:

1.  POST  /{threads-user-id}/threads        → returns container_id
2.  POST  /{threads-user-id}/threads_publish?creation_id=... (after a brief delay)

Required `config`:
    threads_user_id  – Threads user id (numeric, returned during OAuth)
"""
from __future__ import annotations

import asyncio
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

THREADS_API = "https://graph.threads.net/v1.0"


@register_plugin("platform", "threads", api_version="1.0", category="microblog")
class ThreadsPlatform(SocialPlatform):
    display_name = "Threads"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=False, document=False,
        threads=True, scheduling=False, analytics=True,
    )
    max_text_length = 500
    max_hashtags = 10

    def _user_id(self) -> str:
        return (
            self.config.get("threads_user_id")
            or (self.credentials.account_id if self.credentials else "")
        )

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self._access_token():
            raise PlatformValidationError("No Threads access token. Reconnect.")
        if not self._user_id():
            raise PlatformValidationError(
                "No Threads user id. Reconnect this account; the OAuth callback "
                "should populate it from /me on the Threads API.",
            )

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="threads"),
            refresh_token=None,
            scopes=("threads_basic", "threads_content_publish"),
            account_id=self._user_id(),
            account_handle=self.config.get("username"),
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        token = self._access_token()
        user_id = self._user_id()
        text = _compose(payload)

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1 — create container
            data: dict[str, Any] = {
                "access_token": token,
                "text": text,
            }
            if payload.media:
                m = payload.media[0]
                if m.kind == "video":
                    data["media_type"] = "VIDEO"
                    data["video_url"] = m.url
                else:
                    data["media_type"] = "IMAGE"
                    data["image_url"] = m.url
            else:
                data["media_type"] = "TEXT"

            r = await client.post(f"{THREADS_API}/{user_id}/threads", data=data)
            if r.status_code >= 400:
                raise RuntimeError(f"Threads container failed [{r.status_code}]: {r.text}")
            container_id = r.json().get("id", "")
            if not container_id:
                raise RuntimeError(f"Threads container returned no id: {r.text}")

            # Step 2 — Threads recommends waiting ~30s before publish for video.
            await asyncio.sleep(2.0 if not payload.media or payload.media[0].kind != "video" else 30.0)

            r = await client.post(
                f"{THREADS_API}/{user_id}/threads_publish",
                data={"creation_id": container_id, "access_token": token},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"Threads publish failed [{r.status_code}]: {r.text}")
            published_id = r.json().get("id", "")

        log.info("threads_published", user=user_id, post_id=published_id)
        username = self.config.get("username") or "user"
        return PublishResult(
            external_post_id=published_id,
            url=(f"https://www.threads.net/@{username}/post/{published_id}"
                 if published_id else None),
            raw_response={"container_id": container_id},
        )

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        token = self._access_token()
        if not token or not external_post_id:
            return {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    f"{THREADS_API}/{external_post_id}/insights",
                    params={
                        "metric": "views,likes,replies,reposts,quotes",
                        "access_token": token,
                    },
                )
                if r.status_code >= 400:
                    return {}
                data = r.json()
        except httpx.HTTPError as exc:
            log.warning("threads_metrics_failed", error=str(exc))
            return {}
        out: dict[str, Any] = {}
        for entry in data.get("data", []):
            values = entry.get("values", [])
            if values:
                out[entry.get("name", "")] = values[-1].get("value", 0)
        return out


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
