"""Instagram Business adapter — real Graph API publishing.

IG Business publishing is a two-step dance:

1.  POST  /{ig-user-id}/media       → returns container_id
2.  GET   /{container_id}?fields=status_code  → poll until "FINISHED"
3.  POST  /{ig-user-id}/media_publish?creation_id={container_id}

The IG user id is the *Instagram Business Account id* (different from the
FB Page id). After the OAuth callback we look up
``/{page-id}?fields=instagram_business_account`` to find it and stash it on
``config.ig_user_id``.

Required `config`:
    ig_user_id          – Instagram Business Account id (numeric)
    page_access_token   – Page-scoped access token (covers IG too)
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

GRAPH_API = "https://graph.facebook.com/v21.0"


@register_plugin("platform", "instagram", api_version="1.0", category="visual")
class InstagramPlatform(SocialPlatform):
    display_name = "Instagram"
    capabilities = PlatformCapabilities(
        text_only=False,         # IG always needs media
        image=True, video=True, gif=False, document=False,
        threads=False, scheduling=False, analytics=True,
    )
    max_text_length = 2200
    max_hashtags = 30

    # ── helpers ────────────────────────────────────────────────────────
    def _ig_user_id(self) -> str:
        return (
            self.config.get("ig_user_id")
            or self.config.get("instagram_business_account_id")
            or (self.credentials.account_id if self.credentials else "")
        )

    def _token(self) -> str:
        return (
            self.config.get("page_access_token")
            or self._access_token()
        )

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not payload.media:
            raise PlatformValidationError(
                "Instagram requires at least one image or video.",
            )
        if not payload.media[0].url or not payload.media[0].url.startswith("http"):
            raise PlatformValidationError(
                "Instagram media must be hosted on a publicly reachable HTTPS URL "
                "(Meta downloads it server-side).",
            )
        if not self._ig_user_id():
            raise PlatformValidationError(
                "No Instagram Business Account linked. Reconnect this account "
                "and pick the Facebook Page that owns the IG profile.",
            )
        if not self._token():
            raise PlatformValidationError(
                "No access token. Reconnect this account.",
            )

    # ── lifecycle ─────────────────────────────────────────────────────
    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="instagram"),
            refresh_token=None,
            scopes=("instagram_basic", "instagram_content_publish",
                    "pages_show_list", "pages_read_engagement"),
            account_id=self._ig_user_id(),
            account_handle=self.config.get("ig_username"),
        )

    # ── publishing ─────────────────────────────────────────────────────
    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        ig = self._ig_user_id()
        token = self._token()
        caption = _compose(payload)
        media = payload.media[0]

        # Normalize the image to fit Instagram's aspect-ratio range
        # (4:5 portrait to 1.91:1 landscape). Without this, posts with
        # tall flyers / unusual ratios get rejected at container-create
        # with error_subcode 2207009 "Invalid Aspect Ratio". The
        # normalizer pads with a neutral background (preserves all
        # content) and re-hosts in Supabase — so the URL we send IG
        # is always inside the allowed range. Videos skip normalization
        # in this round (Reels need ffmpeg-side work, not Pillow).
        image_url = media.url
        if not _is_video_asset(media):
            from app.services.media_normalizer import MediaNormalizer
            normalizer = MediaNormalizer()
            org_id = str(self.config.get("__org_id__") or "shared")
            image_url = await normalizer.normalize_image_for_platform(
                image_url=media.url,
                platform_plugin_name="instagram",
                org_id=org_id,
            )

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1 — create the media container.
            create_data: dict[str, Any] = {
                "caption": caption,
                "access_token": token,
            }
            if _is_video_asset(media):
                # Reels container — Meta processes asynchronously, so
                # step 2 (poll) is mandatory for video.
                create_data["media_type"] = "REELS"
                create_data["video_url"] = media.url
            else:
                create_data["image_url"] = image_url

            r = await client.post(f"{GRAPH_API}/{ig}/media", data=create_data)
            if r.status_code >= 400:
                raise RuntimeError(f"IG container create failed [{r.status_code}]: {r.text}")
            container_id = r.json().get("id", "")
            if not container_id:
                raise RuntimeError(f"IG container create returned no id: {r.text}")

            # Step 2 — poll until the container is FINISHED (videos take time).
            await _wait_for_container(client, container_id, token)

            # Step 3 — publish the container.
            r = await client.post(
                f"{GRAPH_API}/{ig}/media_publish",
                data={"creation_id": container_id, "access_token": token},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"IG publish failed [{r.status_code}]: {r.text}")
            published_id = r.json().get("id", "")
            log.info("instagram_published", ig_user=ig, post_id=published_id)

        return PublishResult(
            external_post_id=published_id,
            url=f"https://www.instagram.com/p/{published_id}/" if published_id else None,
            raw_response={"container_id": container_id},
        )

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        """Pull insights for an IG media. Two API calls — /insights for
        impressions/reach/saved/interactions, and the media node itself
        for like_count/comments_count which Insights doesn't return.
        Maps to the engagement service's standard schema."""
        token = self._token()
        if not token or not external_post_id:
            return {}

        insights: dict[str, Any] = {}
        node: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Insights — impressions/reach/saved/total_interactions.
                ri = await client.get(
                    f"{GRAPH_API}/{external_post_id}/insights",
                    params={
                        "metric": "impressions,reach,saved,total_interactions",
                        "access_token": token,
                    },
                )
                if ri.status_code < 400:
                    for entry in ri.json().get("data", []):
                        values = entry.get("values") or []
                        if values:
                            insights[entry.get("name", "")] = values[-1].get(
                                "value", 0)

                # Media node — like_count + comments_count.
                rn = await client.get(
                    f"{GRAPH_API}/{external_post_id}",
                    params={
                        "fields": "like_count,comments_count,media_type",
                        "access_token": token,
                    },
                )
                if rn.status_code < 400:
                    node = rn.json() or {}
        except httpx.HTTPError as exc:
            log.warning("instagram_metrics_failed", error=str(exc))
            return {"fetch_error": str(exc)[:200]}

        return {
            "likes":       int(node.get("like_count") or 0),
            "comments":    int(node.get("comments_count") or 0),
            "saves":       int(insights.get("saved") or 0),
            "impressions": int(insights.get("impressions") or 0),
            "reach":       int(insights.get("reach") or 0),
            "extra": {
                "total_interactions": int(insights.get("total_interactions") or 0),
                "media_type": node.get("media_type"),
            },
        }


async def _wait_for_container(
    client: httpx.AsyncClient, container_id: str, token: str,
    *, max_wait: float = 60.0, interval: float = 2.0,
) -> None:
    """IG videos can take ~30s to encode. Poll status_code until FINISHED."""
    elapsed = 0.0
    while elapsed < max_wait:
        r = await client.get(
            f"{GRAPH_API}/{container_id}",
            params={"fields": "status_code", "access_token": token},
        )
        if r.status_code < 400:
            status = r.json().get("status_code", "")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise RuntimeError(f"IG container processing failed: {r.text}")
        await asyncio.sleep(interval)
        elapsed += interval
    raise RuntimeError(f"IG container {container_id} did not finish within {max_wait}s")


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n.\n.\n{tags}".strip()


# ── media-kind sniffer ─────────────────────────────────────────────────
# A simple ``media.kind == "video"`` worked in theory because MediaKind
# is a ``str, Enum`` subclass — but the May 12 2026 incident proved
# that's not enough in practice: a video Post landed at Meta's
# image_url endpoint and got rejected as "image with aspect ratio ()".
# Root cause was a representation drift somewhere in the
# Post → DraftPost → PostPayload pipeline (a dict slipped through
# where a MediaAsset should have been; or the kind field deserialized
# to a plain string in a code path the unit tests didn't cover).
#
# Rather than chase that one buggy callsite, this helper makes the
# IG adapter robust to ANY plausible representation:
#
#   * MediaKind enum                  — the documented contract
#   * Plain string "video"            — what JSON deserialization
#                                       might leave behind
#   * MediaAsset with .kind = None    — defensive
#   * Filename / URL extension        — last-resort sniff for the
#                                       case where ``kind`` was lost
#                                       entirely
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def _is_video_asset(media: Any) -> bool:
    kind = getattr(media, "kind", None)
    if kind is not None:
        # MediaKind is a str-Enum so .value works; raw strings have no
        # .value so we fall through to str().
        kind_value = (
            kind.value if hasattr(kind, "value") else str(kind)
        ).lower()
        if kind_value == "video":
            return True
        if kind_value in ("image", "gif", "document"):
            return False
    # URL-extension sniff — covers the case where kind was dropped /
    # mistyped upstream. Bias toward False for unknown extensions so
    # we never accidentally route an image through the Reels path.
    url = (getattr(media, "url", "") or "").lower().split("?", 1)[0]
    return any(url.endswith(ext) for ext in _VIDEO_EXTENSIONS)
