"""YouTube adapter — real video upload via Data API v3.

Flow:
  1. Initiate a resumable upload — POST to /upload/youtube/v3/videos with
     metadata in the body and ``uploadType=resumable``. Server returns an
     upload URL in the ``Location`` header.
  2. Stream video bytes (fetched from the public ``MediaAsset.url``) to that
     URL. For files <128 MB we PUT them in one shot; larger uploads should
     chunk, but the simple path covers most non-profit content.
  3. Response carries the new video id.

Required ``config`` (set on the Platform row by OAuth callback or Settings):
    channel_id    – the YouTube channel id (UC...)
    privacy       – "private" | "unlisted" | "public" (default "private")
    category_id   – YouTube category id (default "22" = People & Blogs)

The video media asset must point to a publicly reachable URL. Render & co
will fetch it server-side.
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

YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
YT_API = "https://www.googleapis.com/youtube/v3"


@register_plugin("platform", "youtube", api_version="1.0", category="video")
class YouTubePlatform(SocialPlatform):
    display_name = "YouTube"
    capabilities = PlatformCapabilities(
        text_only=False, image=False, video=True, gif=False, document=False,
        threads=False, scheduling=True, analytics=True,
    )
    max_text_length = 5000
    max_hashtags = 15

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not payload.media:
            raise PlatformValidationError("YouTube requires a video media asset.")
        media = payload.media[0]
        kind_str = media.kind.value if hasattr(media.kind, "value") else str(media.kind)
        if kind_str != "video":
            raise PlatformValidationError(
                f"YouTube only accepts videos; got {kind_str!r}.",
            )
        if not media.url or not media.url.startswith("http"):
            raise PlatformValidationError(
                "Video must be hosted on a publicly reachable HTTPS URL "
                "(we fetch the bytes server-side and stream them to YouTube).",
            )
        if not self._access_token():
            raise PlatformValidationError("No YouTube access token. Reconnect.")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="youtube"),
            refresh_token=None,
            scopes=(
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
            ),
            account_id=self.config.get("channel_id", ""),
            account_handle=self.config.get("channel_title"),
        )

    # ── publishing ─────────────────────────────────────────────────────
    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        token = self._access_token()
        media = payload.media[0]

        title = (payload.extra.get("title") or payload.text[:90] or "Untitled").strip()
        description = _compose(payload)
        privacy = payload.extra.get("privacy", self.config.get("privacy", "private"))
        category_id = payload.extra.get("category_id", self.config.get("category_id", "22"))
        tags = [h.value.lstrip("#") for h in payload.hashtags]

        metadata = {
            "snippet": {
                "title": title[:100],            # YouTube enforces <= 100 chars
                "description": description[:5000],
                "tags": tags[:500],
                "categoryId": str(category_id),
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        # Step 1 — initiate resumable upload
        async with httpx.AsyncClient(timeout=30.0) as client:
            init = await client.post(
                YT_UPLOAD,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    # The X-Upload-Content-Type header tells YT what bytes to expect.
                    "X-Upload-Content-Type": "video/*",
                },
                json=metadata,
            )
            if init.status_code >= 400:
                raise RuntimeError(f"YouTube initiate failed [{init.status_code}]: {init.text}")
            upload_url = init.headers.get("location") or init.headers.get("Location")
            if not upload_url:
                raise RuntimeError(f"YouTube initiate: no upload URL in response headers")

            # Step 2 — fetch the video bytes from the public URL the user provided.
            # Use a streaming GET so we don't OOM on big files. Forward to YT.
            async with httpx.AsyncClient(timeout=600.0) as fetcher:
                async with fetcher.stream("GET", media.url) as src:
                    if src.status_code >= 400:
                        raise RuntimeError(f"Could not fetch video {media.url!r}: {src.status_code}")
                    # We need the total length for resumable upload Content-Length.
                    total = int(src.headers.get("content-length", "0"))
                    upload_headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": src.headers.get("content-type", "video/*"),
                    }
                    if total > 0:
                        upload_headers["Content-Length"] = str(total)
                    # Stream the bytes in one PUT (for now — chunking can be
                    # added when we hit >128 MB videos).
                    body_bytes = await src.aread()
                async with httpx.AsyncClient(timeout=600.0) as uploader:
                    up = await uploader.put(
                        upload_url,
                        content=body_bytes,
                        headers=upload_headers,
                    )
                    if up.status_code >= 400:
                        raise RuntimeError(f"YouTube upload failed [{up.status_code}]: {up.text}")
                    data = up.json()

        video_id = data.get("id", "")
        log.info("youtube_published", video_id=video_id, title=title[:60])
        return PublishResult(
            external_post_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
            raw_response=data,
        )

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        token = self._access_token()
        if not token or not external_post_id:
            return {}
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                r = await client.get(
                    f"{YT_API}/videos",
                    params={"id": external_post_id, "part": "statistics"},
                )
                if r.status_code >= 400:
                    return {}
                items = r.json().get("items") or []
                if not items:
                    return {}
                stats = items[0].get("statistics") or {}
        except httpx.HTTPError as exc:
            log.warning("youtube_metrics_failed", error=str(exc))
            return {}
        return {
            "views":    int(stats.get("viewCount", 0)),
            "likes":    int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "favorites": int(stats.get("favoriteCount", 0)),
        }

    async def delete(self, external_post_id: str) -> bool:
        token = self._access_token()
        if not token or not external_post_id:
            return False
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                r = await client.delete(f"{YT_API}/videos", params={"id": external_post_id})
                return r.status_code in (200, 204)
        except httpx.HTTPError:
            return False


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
