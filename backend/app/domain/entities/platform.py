from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..value_objects.credentials import OAuthCredentials
from ..value_objects.ids import OrgId, PlatformId, new_id


class PlatformStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    EXPIRED = "expired"
    ERROR = "error"


@dataclass(slots=True)
class Platform:
    """A *single connected account* on a social network.

    A workspace can hold many `Platform` rows pointing at the *same* `plugin_name`
    — e.g. three different LinkedIn pages, two Instagram business accounts, etc.
    Each row carries its own OAuth tokens, account handle, and per-account config.
    """
    id: PlatformId
    org_id: OrgId
    plugin_name: str           # references SocialPlatform.name in adapter registry
    display_name: str          # user-visible label, e.g. "Acme Corp · Marketing LI"
    account_handle: str | None = None         # @handle / page-id / channel name shown in UI
    account_external_id: str | None = None    # stable platform-side ID for this account
    credentials: OAuthCredentials | None = None
    status: PlatformStatus = PlatformStatus.DISCONNECTED
    config: dict = field(default_factory=dict)
    is_default: bool = False   # default account for this plugin (used when no explicit pick)
    # Free-form labels — drives tag-based fan-out in the targeting layer.
    # e.g. ["marketing", "us-region", "pillar-content"]
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: datetime | None = None

    def mark_connected(self, credentials: OAuthCredentials) -> None:
        self.credentials = credentials
        self.account_handle = credentials.account_handle or self.account_handle
        self.account_external_id = credentials.account_id
        self.status = PlatformStatus.CONNECTED

    def mark_error(self) -> None:
        self.status = PlatformStatus.ERROR

    @classmethod
    def create(cls, *, org_id: OrgId, plugin_name: str, display_name: str,
               account_handle: str | None = None, config: dict | None = None,
               is_default: bool = False, tags: list[str] | None = None) -> "Platform":
        return cls(
            id=PlatformId(new_id()),
            org_id=org_id,
            plugin_name=plugin_name,
            display_name=display_name,
            account_handle=account_handle,
            config=config or {},
            is_default=is_default,
            tags=list(tags or []),
        )
