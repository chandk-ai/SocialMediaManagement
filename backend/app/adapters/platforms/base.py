"""Base contract for social media platform adapters.

Implement this class + register with @register_plugin('platform', '<name>')
and the platform appears throughout the system: workflow editor, agent
fan-out, analytics dashboards.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from app.domain.value_objects.content import Hashtag, MediaAsset
from app.domain.value_objects.credentials import OAuthCredentials


class PlatformValidationError(Exception):
    """Payload failed pre-flight validation for this platform."""


@dataclass(frozen=True, slots=True)
class PlatformCapabilities:
    text_only: bool = True
    image: bool = True
    video: bool = False
    gif: bool = False
    document: bool = False
    threads: bool = False           # X threads, Mastodon, etc.
    scheduling: bool = True         # native scheduling supported
    analytics: bool = True
    experimental: bool = False      # set True until publish() actually hits the real API


class PlatformNotImplemented(Exception):
    """Raised by an experimental adapter's publish() to prevent silent fakes."""


@dataclass(frozen=True, slots=True)
class PostPayload:
    text: str
    hashtags: list[Hashtag] = field(default_factory=list)
    media: list[MediaAsset] = field(default_factory=list)
    scheduled_for: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PublishResult:
    external_post_id: str
    url: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


class SocialPlatform(ABC):
    """Plug-and-play interface for posting to a social network."""

    # ── class-level metadata (read by frontend & validators) ──────────
    plugin_name: ClassVar[str] = ""           # set by @register_plugin
    api_version: ClassVar[str] = "1.0"
    display_name: ClassVar[str] = ""
    capabilities: ClassVar[PlatformCapabilities] = PlatformCapabilities()
    max_text_length: ClassVar[int] = 3000
    max_hashtags: ClassVar[int] = 30

    def __init__(self, credentials: OAuthCredentials | None = None,
                 config: dict[str, Any] | None = None) -> None:
        self.credentials = credentials
        self.config = config or {}

    # ── lifecycle ─────────────────────────────────────────────────────
    @abstractmethod
    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        """Exchange an OAuth authorization code for credentials."""

    async def refresh_credentials(self) -> OAuthCredentials:
        """Override if the platform supports refresh tokens."""
        if self.credentials is None:
            raise RuntimeError("No credentials to refresh")
        return self.credentials

    # ── publishing ────────────────────────────────────────────────────
    def validate(self, payload: PostPayload) -> None:
        """Default validation. Override for richer rules."""
        if len(payload.text) > self.max_text_length:
            raise PlatformValidationError(
                f"text length {len(payload.text)} exceeds max {self.max_text_length}"
            )
        if len(payload.hashtags) > self.max_hashtags:
            raise PlatformValidationError(
                f"too many hashtags: {len(payload.hashtags)} > {self.max_hashtags}"
            )
        if payload.media and not (
            self.capabilities.image or self.capabilities.video or self.capabilities.gif
        ):
            raise PlatformValidationError("platform does not support media")

    @abstractmethod
    async def publish(self, payload: PostPayload) -> PublishResult: ...

    # ── optional ──────────────────────────────────────────────────────
    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        return {}

    async def delete(self, external_post_id: str) -> bool:
        return False

    # ── helpers shared by all adapters ────────────────────────────────
    def _access_token(self) -> str:
        """Decrypt the access token. Returns '' if no credentials set."""
        if not self.credentials or not self.credentials.access_token:
            return ""
        from app.core.secrets import build_token_vault
        try:
            return build_token_vault().decrypt(self.credentials.access_token)
        except Exception:                                   # noqa: BLE001
            return ""

    def _require_real(self) -> None:
        """Adapters still in 'reference implementation' mode call this in
        publish() to refuse silent fakes. Real adapters override or skip."""
        if self.capabilities.experimental:
            raise PlatformNotImplemented(
                f"{self.display_name or self.plugin_name} publishing is not yet "
                f"implemented end-to-end. Posts targeted at this platform are "
                f"blocked until a real API integration is wired up."
            )
