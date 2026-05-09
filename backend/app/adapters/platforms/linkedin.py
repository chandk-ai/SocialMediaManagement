"""LinkedIn adapter — real UGC Post publishing.

Uses the modern ``/rest/posts`` endpoint (preferred over the older /v2/ugcPosts).
Author URN is derived from `credentials.account_id` after OAuth (e.g.
``urn:li:person:abc123`` for personal posts, ``urn:li:organization:123`` for
company pages — set during OAuth callback based on which option the user picked).

Image / video upload is a 3-step dance via /rest/images and /rest/videos
(register → upload bytes → reference in the post body). For now we support
text + image-by-URL (downloaded server-side) which covers most non-profit use
cases. Multi-image carousels are TODO.
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

LI_REST = "https://api.linkedin.com/rest"
LI_API_VERSION = "202411"     # Linkedin requires versioned headers


@register_plugin("platform", "linkedin", api_version="1.0", category="professional")
class LinkedInPlatform(SocialPlatform):
    display_name = "LinkedIn"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=False, document=True,
        threads=False, scheduling=False, analytics=True,
    )
    max_text_length = 3000
    max_hashtags = 30

    # ── helpers ────────────────────────────────────────────────────────
    def _author_urn(self) -> str:
        """`urn:li:person:xxxx` or `urn:li:organization:xxxx`."""
        author = (self.credentials.account_id if self.credentials else "")
        if author and not author.startswith("urn:li:"):
            # Default: assume personal post if not URN-formatted yet.
            author = f"urn:li:person:{author}"
        return author

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": LI_API_VERSION,
            "Content-Type": "application/json",
        }

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self._access_token():
            raise PlatformValidationError(
                "No LinkedIn access token. Reconnect this account.",
            )
        if not self._author_urn():
            raise PlatformValidationError(
                "No LinkedIn author URN. Reconnect to populate credentials.",
            )

    # ── lifecycle ─────────────────────────────────────────────────────
    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="linkedin"),
            refresh_token=None,
            scopes=("openid", "profile", "email", "w_member_social"),
            account_id=self._author_urn(),
            account_handle=self.config.get("name"),
        )

    # ── publishing ─────────────────────────────────────────────────────
    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        token = self._access_token()
        author = self._author_urn()
        text = _compose(payload)

        async with httpx.AsyncClient(timeout=30.0, headers=self._headers(token)) as client:
            content: dict[str, Any] = {}
            if payload.media:
                # Image-by-URL path: register an upload, then POST the image bytes.
                # See: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api
                image_urn = await _upload_image_from_url(client, author, payload.media[0].url)
                content = {"media": {"id": image_urn, "altText": ""}}

            body = {
                "author": author,
                "commentary": text,
                "visibility": "PUBLIC",
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
            }
            if content:
                body["content"] = content

            r = await client.post(f"{LI_REST}/posts", json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"LinkedIn publish failed [{r.status_code}]: {r.text}")
            # The post URN is returned in the X-RestLi-Id header.
            post_urn = r.headers.get("x-restli-id") or r.headers.get("X-RestLi-Id") or ""
            log.info("linkedin_published", author=author, urn=post_urn)

        return PublishResult(
            external_post_id=post_urn,
            url=f"https://www.linkedin.com/feed/update/{post_urn}/" if post_urn else None,
            raw_response={"status": r.status_code},
        )

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        """Pull the post's reaction + comment counts. LinkedIn doesn't
        expose share or impression counts on /socialActions for v2 UGC
        posts (those live behind /organizationalEntityShareStatistics
        which requires a different scope and only works for company
        pages). We fetch what's available and normalise to the engagement
        service's standard schema (likes / comments / shares / …)."""
        token = self._access_token()
        if not token or not external_post_id:
            return {}
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=self._headers(token)) as client:
                r = await client.get(f"{LI_REST}/socialActions/{external_post_id}")
                if r.status_code >= 400:
                    return {"fetch_error": f"linkedin_{r.status_code}"}
                data = r.json()
        except httpx.HTTPError as exc:
            log.warning("linkedin_metrics_failed", error=str(exc))
            return {"fetch_error": str(exc)[:200]}

        likes = int((data.get("likesSummary") or {}).get("totalLikes") or 0)
        comments = int(
            (data.get("commentsSummary") or {}).get("totalFirstLevelComments") or 0,
        )
        return {
            "likes": likes,
            "comments": comments,
            # The remaining standard fields LinkedIn doesn't expose on
            # this endpoint — leave None so the rollup ignores them
            # rather than treating zero as "we measured zero shares."
            "shares": None,
            "impressions": None,
            "reach": None,
            "clicks": None,
            "extra": {
                "raw": {
                    "likesSummary": data.get("likesSummary"),
                    "commentsSummary": data.get("commentsSummary"),
                },
            },
        }


async def _upload_image_from_url(
    client: httpx.AsyncClient, author: str, image_url: str,
) -> str:
    """Register an image upload, then POST the bytes. Returns the image URN."""
    # 1. Initialize upload
    init = await client.post(
        f"{LI_REST}/images?action=initializeUpload",
        json={"initializeUploadRequest": {"owner": author}},
    )
    if init.status_code >= 400:
        raise RuntimeError(f"LinkedIn image initializeUpload failed: {init.text}")
    val = init.json().get("value", {})
    upload_url = val.get("uploadUrl")
    image_urn = val.get("image", "")
    if not upload_url or not image_urn:
        raise RuntimeError(f"LinkedIn image init returned no uploadUrl/image: {init.text}")

    # 2. Fetch image bytes from the public URL the user provided
    fetched = await client.get(image_url, timeout=30.0)
    fetched.raise_for_status()

    # 3. PUT bytes to LinkedIn's upload URL (different host; bare PUT)
    async with httpx.AsyncClient(timeout=60.0) as raw_client:
        up = await raw_client.put(
            upload_url, content=fetched.content,
            headers={"Content-Type": fetched.headers.get("content-type", "application/octet-stream")},
        )
    if up.status_code >= 400:
        raise RuntimeError(f"LinkedIn image upload PUT failed [{up.status_code}]: {up.text}")
    return image_urn


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
