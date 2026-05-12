"""Media import + upload endpoints.

Thin HTTP wrappers around ``MediaImportService`` — the service holds
the real logic so it can be invoked directly from source plugins
(Notion / Drive / RSS / …) without paying for an in-process HTTP
round-trip.

Two endpoints:

* ``POST /media/upload``   — multipart file from the user's device.
* ``POST /media/import``   — paste a YouTube/TikTok/Vimeo/direct URL
                              and we'll fetch + re-host it.

Both return a permanent public Supabase Storage URL that platforms
(Meta, Pinterest, …) can fetch server-side at any future moment.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import current_user
from app.core.logging import get_logger
from app.core.security import Principal
from app.services.media_import import (
    MAX_BYTES,
    MediaImportError,
    MediaImportService,
)

log = get_logger(__name__)
router = APIRouter()


class MediaOut(BaseModel):
    url: str
    kind: str = Field(description="image | video")
    content_type: str
    size_bytes: int
    storage_path: str


def _to_out(result) -> MediaOut:
    return MediaOut(
        url=result.url,
        kind=result.kind,
        content_type=result.content_type,
        size_bytes=result.size_bytes,
        storage_path=result.storage_path,
    )


# ── POST /media/upload — multipart from the user's device ──────────────
@router.post("/upload", response_model=MediaOut)
async def upload_media(
    file: UploadFile = File(...),
    user: Principal = Depends(current_user),
) -> MediaOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")

    # Stream-and-cap so we don't buffer ~100 MB in memory on a rejected
    # oversized upload. The service does final validation.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds {MAX_BYTES // (1024*1024)} MB limit.",
            )
        chunks.append(chunk)

    svc = MediaImportService()
    try:
        result = await svc.import_bytes(
            org_id=user.org_id,
            data=b"".join(chunks),
            content_type=(file.content_type or "application/octet-stream"),
            filename=file.filename,
        )
    except MediaImportError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return _to_out(result)


# ── POST /media/import — YouTube / TikTok / direct URL → MP4 ───────────
class ImportBody(BaseModel):
    url: str
    max_height: int = 1080


@router.post("/import", response_model=MediaOut)
async def import_media(
    body: ImportBody,
    user: Principal = Depends(current_user),
) -> MediaOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")

    svc = MediaImportService()

    # Two strategies in order:
    #   1. If the URL is a direct-fetchable asset (image/* or video/*
    #      content-type), grab the bytes — cheap & fast.
    #   2. Otherwise fall back to yt-dlp (HTML pages, embedded
    #      players, etc.).
    # The first attempt fails fast on non-binary content-types, so the
    # fall-through cost is minimal.
    try:
        return _to_out(
            await svc.import_url(org_id=user.org_id, url=body.url),
        )
    except MediaImportError as direct_exc:
        log.info("media_import_direct_failed_falling_back_to_ytdlp",
                 reason=str(direct_exc))

    try:
        return _to_out(
            await svc.import_via_ytdlp(
                org_id=user.org_id, url=body.url, max_height=body.max_height,
            ),
        )
    except MediaImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
