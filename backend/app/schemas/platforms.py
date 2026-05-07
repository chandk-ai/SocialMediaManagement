from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .common import APIModel


class PlatformCreate(APIModel):
    plugin_name: str = Field(..., examples=["linkedin"])
    display_name: str = Field(..., examples=["Acme Corp · Marketing LI"])
    account_handle: str | None = Field(None, examples=["@acme-marketing"])
    config: dict = Field(default_factory=dict)
    is_default: bool = False
    tags: list[str] = Field(default_factory=list, examples=[["marketing", "us-region"]])


class PlatformOut(APIModel):
    id: UUID
    plugin_name: str
    display_name: str
    account_handle: str | None = None
    account_external_id: str | None = None
    status: str
    config: dict
    is_default: bool = False
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    last_used_at: datetime | None = None


class PlatformGroupOut(APIModel):
    """Returned by /platforms/grouped — UI uses this to render multi-account cards."""
    plugin_name: str
    display_name: str          # plugin display name (e.g. "Instagram")
    accounts: list[PlatformOut]


class OAuthInitOut(APIModel):
    authorize_url: str
    state: str
