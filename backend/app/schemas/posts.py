from __future__ import annotations

from datetime import datetime
from uuid import UUID

from .common import APIModel


class MediaOut(APIModel):
    url: str
    kind: str = "image"
    alt_text: str | None = None


class PostOut(APIModel):
    id: UUID
    workflow_id: UUID
    run_id: UUID
    platform_id: UUID
    # ── Target denormalization ───────────────────────────────────────
    # These three fields are resolved server-side from the Platform row
    # so the UI can render a target chip (logo + display name + handle)
    # without making an N+1 round-trip per post. ``platform_plugin_name``
    # is the registry key the frontend uses to look up the brand icon.
    # Nullable so legacy rows (where the platform was later deleted)
    # still serialize cleanly.
    platform_plugin_name: str | None = None
    platform_display_name: str | None = None
    account_handle: str | None = None
    text: str
    hashtags: list[str]
    status: str
    scheduled_for: datetime | None
    published_at: datetime | None
    external_post_id: str | None
    error: str | None
    created_at: datetime
    # Media attachments — empty list when there's nothing attached. The
    # Posts UI needs this to render thumbnails and to repopulate the
    # Edit form when the user opens an existing post for re-editing.
    media: list[MediaOut] = []
