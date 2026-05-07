"""Bluesky adapter — real ATProto publishing via PDS HTTP API.

We avoid the heavy ``atproto`` Python SDK (which pulls in a lot of CBOR/IPFS
deps) and just talk HTTP to the PDS directly:

  1. POST /xrpc/com.atproto.server.createSession   → access JWT
  2. POST /xrpc/com.atproto.repo.createRecord      → publish a post

Required ``config`` (no OAuth — Bluesky uses app-passwords):
    handle         – your handle without leading @ (e.g. "alice.bsky.social")
    app_password   – an app-specific password from Settings → App Passwords
    pds_url        – optional, defaults to https://bsky.social
"""
from __future__ import annotations

from datetime import datetime, timezone

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

DEFAULT_PDS = "https://bsky.social"


@register_plugin("platform", "bluesky", api_version="1.0", category="microblog")
class BlueskyPlatform(SocialPlatform):
    display_name = "Bluesky"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=False, gif=False, document=False,
        threads=True, scheduling=False, analytics=False,
    )
    max_text_length = 300
    max_hashtags = 10

    config_schema = {
        "type": "object",
        "required": ["handle", "app_password"],
        "properties": {
            "handle": {
                "type": "string",
                "title": "Handle",
                "description": "Your Bluesky handle, e.g. alice.bsky.social (no @).",
            },
            "app_password": {
                "type": "string", "format": "password",
                "title": "App password",
                "description": "Settings → App Passwords → Create. Don't use your account password.",
            },
            "pds_url": {
                "type": "string",
                "title": "PDS URL (optional)",
                "default": DEFAULT_PDS,
                "description": "Only change if you self-host or use a custom PDS.",
            },
        },
    }

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self.config.get("handle"):
            raise PlatformValidationError("handle is required (e.g. alice.bsky.social)")
        if not self.config.get("app_password"):
            raise PlatformValidationError("app_password is required")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="bluesky"),
            refresh_token=None,
            scopes=("post:create",),
            account_id=self.config.get("handle", ""),
            account_handle=self.config.get("handle"),
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        pds = self.config.get("pds_url") or DEFAULT_PDS
        handle = self.config["handle"]
        text = _compose(payload)

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1 — create session
            sess = await client.post(
                f"{pds}/xrpc/com.atproto.server.createSession",
                json={"identifier": handle, "password": self.config["app_password"]},
            )
            if sess.status_code >= 400:
                raise RuntimeError(f"Bluesky login failed [{sess.status_code}]: {sess.text}")
            session = sess.json()
            jwt = session["accessJwt"]
            did = session["did"]

            # Step 2 — create the post record
            record = {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            r = await client.post(
                f"{pds}/xrpc/com.atproto.repo.createRecord",
                headers={"Authorization": f"Bearer {jwt}"},
                json={
                    "repo": did,
                    "collection": "app.bsky.feed.post",
                    "record": record,
                },
            )
            if r.status_code >= 400:
                raise RuntimeError(f"Bluesky post failed [{r.status_code}]: {r.text}")
            data = r.json()

        # uri looks like: at://did:plc:.../app.bsky.feed.post/3kabc123
        uri = data.get("uri", "")
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        log.info("bluesky_published", uri=uri)
        return PublishResult(
            external_post_id=uri,
            url=f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else None,
            raw_response=data,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n{tags}".strip()
