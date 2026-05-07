"""Tumblr adapter — real publish via NPF v2 API.

POST /v2/blog/{blog-identifier}/posts with NPF (Neue Post Format) JSON.
OAuth-based; the access token comes from the OAuth callback.
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

TUMBLR_API = "https://api.tumblr.com/v2"


@register_plugin("platform", "tumblr", api_version="1.0", category="microblog")
class TumblrPlatform(SocialPlatform):
    display_name = "Tumblr"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=True, document=False,
        threads=False, scheduling=True, analytics=False,
    )
    max_text_length = 4096
    max_hashtags = 30

    config_schema = {
        "type": "object",
        "required": ["blog_identifier"],
        "properties": {
            "blog_identifier": {
                "type": "string",
                "title": "Blog identifier",
                "description": "Your blog's hostname, e.g. yourblog.tumblr.com",
            },
        },
    }

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self.config.get("blog_identifier"):
            raise PlatformValidationError("blog_identifier is required (e.g. yourblog.tumblr.com)")
        if not self._access_token():
            raise PlatformValidationError("No Tumblr access token. Reconnect.")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="tumblr"),
            refresh_token=None,
            scopes=("write",),
            account_id=self.config.get("blog_identifier", ""),
            account_handle=self.config.get("blog_identifier"),
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        token = self._access_token()
        blog = self.config["blog_identifier"]

        # NPF: a post is a list of "content blocks". For text + optional image:
        content: list[dict[str, Any]] = [{"type": "text", "text": payload.text}]
        if payload.media:
            kind = payload.media[0].kind.value if hasattr(payload.media[0].kind, "value") else str(payload.media[0].kind)
            if kind == "image":
                content.append({"type": "image", "media": [{"url": payload.media[0].url}]})
            elif kind == "video":
                content.append({"type": "video", "media": [{"url": payload.media[0].url}]})

        body = {
            "content": content,
            "tags": [h.value.lstrip("#") for h in payload.hashtags],
        }

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        ) as client:
            r = await client.post(f"{TUMBLR_API}/blog/{blog}/posts", json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"Tumblr publish failed [{r.status_code}]: {r.text}")
            data = r.json()

        d = data.get("response") or {}
        post_id = str(d.get("id", "") or d.get("id_string", ""))
        log.info("tumblr_published", blog=blog, id=post_id)
        return PublishResult(
            external_post_id=post_id,
            url=f"https://{blog}/post/{post_id}" if post_id else None,
            raw_response=data,
        )
