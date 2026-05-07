"""Pinterest adapter — real publish via /v5/pins.

After OAuth, posts go to a specific board on the user's account. The board
id is stored in ``config.board_id``.
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

PINTEREST_API = "https://api.pinterest.com/v5"


@register_plugin("platform", "pinterest", api_version="1.0", category="visual")
class PinterestPlatform(SocialPlatform):
    display_name = "Pinterest"
    capabilities = PlatformCapabilities(
        text_only=False, image=True, video=True, gif=False, document=False,
        threads=False, scheduling=False, analytics=True,
    )
    max_text_length = 800
    max_hashtags = 20

    config_schema = {
        "type": "object",
        "required": ["board_id"],
        "properties": {
            "board_id": {
                "type": "string",
                "title": "Board ID",
                "description": "Numeric board id from /v5/boards. The pin will land here.",
            },
        },
    }

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not payload.media:
            raise PlatformValidationError("Pinterest requires an image or video.")
        if not payload.media[0].url:
            raise PlatformValidationError("Media must have a public URL.")
        if not self.config.get("board_id"):
            raise PlatformValidationError("board_id is required.")
        if not self._access_token():
            raise PlatformValidationError("No Pinterest access token. Reconnect.")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="pinterest"),
            refresh_token=None,
            scopes=("pins:read", "pins:write", "boards:read"),
            account_id=self.config.get("board_id", ""),
            account_handle=None,
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        token = self._access_token()
        media = payload.media[0]
        kind = media.kind.value if hasattr(media.kind, "value") else str(media.kind)

        title = (payload.extra.get("title") or payload.text[:100] or "Pin").strip()
        body: dict[str, Any] = {
            "board_id": self.config["board_id"],
            "title": title[:100],
            "description": _compose(payload)[:800],
            "alt_text": payload.extra.get("alt_text", "")[:500],
            "link": payload.extra.get("link"),
        }
        if kind == "video":
            body["media_source"] = {
                "source_type": "video_url",
                "url": media.url,
                "cover_image_url": payload.extra.get("cover_image_url"),
            }
        else:
            body["media_source"] = {
                "source_type": "image_url",
                "url": media.url,
            }

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        ) as client:
            r = await client.post(f"{PINTEREST_API}/pins", json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"Pinterest pin failed [{r.status_code}]: {r.text}")
            data = r.json()

        pin_id = data.get("id", "")
        log.info("pinterest_published", pin_id=pin_id)
        return PublishResult(
            external_post_id=pin_id,
            url=f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None,
            raw_response=data,
        )

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        token = self._access_token()
        if not token or not external_post_id:
            return {}
        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers={"Authorization": f"Bearer {token}"},
            ) as client:
                r = await client.get(
                    f"{PINTEREST_API}/pins/{external_post_id}/analytics",
                    params={"metric_types": "IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK"},
                )
                if r.status_code >= 400:
                    return {}
                d = r.json()
        except httpx.HTTPError:
            return {}
        # Flatten the lifetime totals from the response
        out = {}
        for metric, val in d.items():
            if isinstance(val, dict):
                out[metric.lower()] = val.get("lifetime", 0)
        return out


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
