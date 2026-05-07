"""LinkedIn adapter — UGC posts via the LinkedIn Marketing/REST API.

This is a reference implementation. To go live: drop in your client_id/secret
and switch the publish() body to the real `https://api.linkedin.com/v2/ugcPosts`
call. The contract / signatures stay the same.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domain.value_objects.credentials import EncryptedToken, OAuthCredentials
from app.plugins.registry import register_plugin

from .base import (
    PlatformCapabilities,
    PostPayload,
    PublishResult,
    SocialPlatform,
)

log = get_logger(__name__)


@register_plugin("platform", "linkedin", api_version="1.0", category="professional")
class LinkedInPlatform(SocialPlatform):
    display_name = "LinkedIn"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=False, document=True,
        threads=False, scheduling=True, analytics=True,
    )
    max_text_length = 3000
    max_hashtags = 30

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        # Real flow: POST https://www.linkedin.com/oauth/v2/accessToken
        # body=grant_type=authorization_code&code=...&redirect_uri=...&client_id=...&client_secret=...
        log.info("linkedin_oauth_exchange", code=oauth_code[:6] + "…")
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="linkedin"),
            refresh_token=None,
            scopes=("w_member_social", "r_basicprofile"),
            account_id="urn:li:person:demo",
            account_handle="demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        # Construct the UGC post body per LinkedIn spec
        body: dict[str, Any] = {
            "author": self.credentials.account_id if self.credentials else "urn:li:person:demo",
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": _compose_text(payload)},
                    "shareMediaCategory": "NONE" if not payload.media else "IMAGE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        log.info("linkedin_publish", chars=len(payload.text), media=len(payload.media))
        external_id = f"urn:li:share:{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(external_post_id=external_id,
                             url=f"https://www.linkedin.com/feed/update/{external_id}",
                             raw_response=body)

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        return {"impressions": 0, "clicks": 0, "reactions": 0, "comments": 0}


def _compose_text(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
