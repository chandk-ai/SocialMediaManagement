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
    _safe_org_path,
    _sniff_mime_from_name,
)
from app.infrastructure.supabase.storage import SupabaseStorage

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


# ── POST /media/signed-upload — direct-to-Supabase escape hatch ────────
class SignedUploadIn(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"


class SignedUploadOut(BaseModel):
    signed_url: str = Field(
        description="PUT the file body here; valid for ~2 hours.",
    )
    public_url: str = Field(
        description="The permanent public URL the post should reference "
                    "after the PUT completes successfully.",
    )
    storage_path: str
    content_type: str


# Allowed mime list mirrors MediaImportService — keep them in sync if
# either changes. Pre-validating here means the user gets a clean 415
# instead of a Supabase rejection mid-upload.
_SIGNED_UPLOAD_ALLOWED_MIMES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
    "video/mp4", "video/quicktime",
}


@router.post("/signed-upload", response_model=SignedUploadOut)
async def signed_upload(
    body: SignedUploadIn,
    user: Principal = Depends(current_user),
) -> SignedUploadOut:
    """Get a short-lived Supabase signed upload URL the browser can
    PUT a file to directly. This is the preferred path for any file
    over ~4 MB because the file bytes never traverse our backend (no
    memory cost) and never go through the Vercel proxy (no 4.5 MB
    cap). Smaller files can still use ``POST /media/upload`` for
    simplicity.

    The frontend flow:
      1. POST here with ``{filename, content_type}``  — tiny JSON,
         fits through any proxy.
      2. ``PUT signed_url`` with the file body (and the correct
         ``Content-Type`` header).
      3. After 200, use ``public_url`` as the post's media URL.

    Validation here is intentionally loose — we trust the client
    less than the Storage server. We pre-flight the mime against
    our allow-list so the user sees a clean error if their file
    type is unsupported; size enforcement happens at the Supabase
    bucket level (``file_size_limit``), not here.
    """
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")

    ct = (body.content_type or "application/octet-stream").lower().split(";")[0].strip()
    if ct not in _SIGNED_UPLOAD_ALLOWED_MIMES:
        # Same fallback as the buffered upload: macOS / iPhone often
        # mis-reports content_type; sniff from filename before giving up.
        sniffed = _sniff_mime_from_name(body.filename or "")
        if sniffed in _SIGNED_UPLOAD_ALLOWED_MIMES:
            ct = sniffed
        else:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"Unsupported media type {ct!r}. "
                    f"Allowed: {sorted(_SIGNED_UPLOAD_ALLOWED_MIMES)}."
                ),
            )

    path = _safe_org_path(user.org_id, body.filename or "asset")
    storage = SupabaseStorage()
    try:
        result = await storage.create_signed_upload_url(path)
    except Exception as exc:                                          # noqa: BLE001
        log.exception("signed_upload_url_failed", path=path)
        raise HTTPException(
            status_code=502,
            detail=f"Could not create signed upload URL: {exc}",
        ) from exc

    return SignedUploadOut(
        signed_url=result["signed_url"],
        public_url=result["public_url"],
        storage_path=result["storage_path"],
        content_type=ct,
    )


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
