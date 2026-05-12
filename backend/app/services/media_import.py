"""MediaImportService — single place that knows how to turn an
arbitrary external URL (or yt-dlp-fetchable resource) into a permanent
public asset in Supabase Storage.

Two reasons this is a service, not just an HTTP endpoint:

1. **Source plugins need it.** Notion serves images as short-lived
   signed URLs that expire in ~1 hour; Google Drive serves images as
   HTML "viewer" pages, not bytes; YouTube serves video only through
   yt-dlp. To populate ``SourceItem.media`` with URLs that will still
   resolve when Instagram fetches them server-side three days later,
   the source plugin has to *re-host* the asset somewhere stable
   (our Supabase bucket). A FastAPI route can't easily be called from
   inside another route's request handler, so we extract the work
   into a service the plugins can ``import`` directly.

2. **One place to audit / rate-limit.** Every media import — whether
   from the Edit Post UI, the Notion source, or a yt-dlp re-host —
   goes through here. Future quota tracking, virus scanning, EXIF
   stripping, etc. plug in at this seam.

What it does NOT do:
  * Persist anything in the ``posts`` table (callers shoulder that).
  * Decide whether the imported URL is appropriate for a given
    platform (size, aspect ratio) — that's the platform adapter's
    ``validate()``.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

import httpx

from app.core.logging import get_logger
from app.infrastructure.supabase.storage import SupabaseStorage

log = get_logger(__name__)


# ── Limits ──────────────────────────────────────────────────────────
# 500 MB ceiling — comfortably covers LinkedIn (200 MB), Facebook
# (1.75 GB if needed, capped here), and Twitter (512 MB) video posts
# while leaving headroom. Instagram still rejects above 100 MB at
# publish time, which is enforced by the IG adapter's ``validate()``
# rather than this generic cap. Update both this value AND the
# Supabase bucket's ``file_size_limit`` (in storage.buckets) together
# if you change it — they need to agree.
MAX_BYTES = 500 * 1024 * 1024                     # 500 MB
HTTP_FETCH_TIMEOUT = 300.0                        # seconds (5 min for big videos)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")

# MIME → file extension. Anything not listed falls back to a generic
# extension so the asset is at least retrievable.
_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",          # some servers misreport
    "image/png":  ".png",
    "image/webp": ".webp",
    "image/gif":  ".gif",
    "video/mp4":  ".mp4",
    "video/quicktime": ".mov",
    "application/octet-stream": ".bin",
}

# Permissive list — platforms vary in what they'll accept downstream;
# we just keep obviously-bad uploads out of the bucket.
_ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
_ALLOWED_VIDEO_MIMES = {"video/mp4", "video/quicktime"}
_ALLOWED_MIMES = _ALLOWED_IMAGE_MIMES | _ALLOWED_VIDEO_MIMES


@dataclass(frozen=True, slots=True)
class MediaImportResult:
    """What every import returns. ``url`` is the stable public URL
    callers should persist on ``Post.media`` / ``SourceItem.media``."""
    url: str
    storage_path: str
    content_type: str
    kind: str                 # "image" | "video"
    size_bytes: int
    source_url: str | None = None


class MediaImportError(Exception):
    """Caller-friendly failure with a short message safe to surface
    in API responses and audit-trail events."""


def _kind_for(mime: str) -> str:
    return "video" if mime in _ALLOWED_VIDEO_MIMES else "image"


def _safe_org_path(org_id: str, filename: str) -> str:
    """org-scoped, traversal-safe storage key. The UUID prefix means
    two users uploading the same filename don't clobber each other."""
    safe = _SAFE_NAME_RE.sub("_", filename)[:120] or "asset"
    return f"org/{org_id}/{uuid4()}-{safe}"


class MediaImportService:
    """Synchronous-style facade — every call returns a ``MediaImportResult``
    once the asset is safely in Supabase Storage."""

    def __init__(self, storage: SupabaseStorage | None = None) -> None:
        self.storage = storage or SupabaseStorage()

    # ── 1. Direct bytes (used by the file-upload endpoint) ─────────
    async def import_bytes(
        self, *,
        org_id: str,
        data: bytes,
        content_type: str,
        filename: str | None = None,
        source_url: str | None = None,
    ) -> MediaImportResult:
        """Persist already-in-memory bytes. Caller has done its own
        size check (e.g. streaming upload guard). We re-validate the
        mime here so the endpoint and the source-plugin call site
        share the same allow-list.

        Browsers on macOS sometimes hand us a generic / wrong
        ``content_type`` (e.g. ``text/plain`` for a ``.png`` saved
        from a screenshot, or ``application/octet-stream`` for a
        QuickTime ``.mov``). Falling back to a filename-extension
        sniff covers those cases without trusting the client header
        blindly — we still only ever accept mimes from the allow-list.
        """
        ct = (content_type or "application/octet-stream").lower().split(";")[0].strip()
        if ct not in _ALLOWED_MIMES:
            sniffed = _sniff_mime_from_name(filename or "")
            if sniffed in _ALLOWED_MIMES:
                log.info("media_import_mime_recovered",
                         declared=ct, sniffed=sniffed, filename=filename)
                ct = sniffed
            else:
                raise MediaImportError(
                    f"Unsupported media type {ct!r}. "
                    f"Allowed: {sorted(_ALLOWED_MIMES)}. "
                    f"If the file is an image / video, rename it with the "
                    f"correct extension (.jpg / .png / .webp / .gif / .mp4 / "
                    f".mov) and try again."
                )
        size = len(data)
        if size > MAX_BYTES:
            raise MediaImportError(
                f"File is {size // (1024*1024)} MB — exceeds "
                f"{MAX_BYTES // (1024*1024)} MB limit.",
            )

        name = filename or f"asset{_EXT_BY_MIME.get(ct, '')}"
        path = _safe_org_path(org_id, name)
        try:
            await self.storage.upload(path, data, content_type=ct)
        except Exception as exc:                                      # noqa: BLE001
            log.exception("media_import_bytes_failed",
                          org_id=org_id, path=path, size=size)
            raise MediaImportError(f"Storage upload failed: {exc}") from exc
        return MediaImportResult(
            url=self.storage.public_url(path),
            storage_path=path,
            content_type=ct,
            kind=_kind_for(ct),
            size_bytes=size,
            source_url=source_url,
        )

    # ── 2. Remote URL → fetch → upload  ────────────────────────────
    async def import_url(
        self, *,
        org_id: str,
        url: str,
        headers: dict[str, str] | None = None,
        filename_hint: str | None = None,
        max_bytes: int = MAX_BYTES,
    ) -> MediaImportResult:
        """HTTP GET an external URL (Notion signed URL, Drive direct-
        download, RSS enclosure, S3 presigned, etc.) and put the bytes
        into Supabase Storage.

        ``max_bytes`` is a per-request override — useful when a caller
        knows it's importing thumbnails (small) and wants to fail fast.
        """
        if not url.startswith(("http://", "https://")):
            raise MediaImportError("URL must be http(s)://")

        try:
            async with httpx.AsyncClient(
                timeout=HTTP_FETCH_TIMEOUT, follow_redirects=True,
            ) as client:
                # Stream so we can short-circuit on size before holding
                # ~100 MB in memory unnecessarily.
                async with client.stream("GET", url, headers=headers or {}) as r:
                    r.raise_for_status()
                    content_type = (
                        r.headers.get("content-type", "application/octet-stream")
                        .split(";")[0].strip().lower()
                    )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in r.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise MediaImportError(
                                f"Remote file exceeds {max_bytes // (1024*1024)} MB cap.",
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
        except httpx.HTTPError as exc:
            raise MediaImportError(f"Fetch failed: {exc}") from exc

        # Some servers (notably Notion S3, raw GitHub) hand back
        # ``application/octet-stream`` instead of the real mime. Sniff
        # from the URL path / hint as a fallback so we don't drop the
        # file on the floor.
        if content_type not in _ALLOWED_MIMES:
            sniffed = _sniff_mime_from_name(filename_hint or url)
            if sniffed in _ALLOWED_MIMES:
                content_type = sniffed

        name = filename_hint or _filename_from_url(url) or "asset"
        return await self.import_bytes(
            org_id=org_id, data=body,
            content_type=content_type,
            filename=name, source_url=url,
        )

    # ── 3. yt-dlp pipeline (YouTube / TikTok / Vimeo / …) ──────────
    async def import_via_ytdlp(
        self, *,
        org_id: str,
        url: str,
        max_height: int = 1080,
        title_hint: str | None = None,
    ) -> MediaImportResult:
        """Download a video URL that *only* yt-dlp can resolve to MP4
        (because the host serves HTML, not bytes). Re-hosts in Supabase
        so platforms can fetch the resulting MP4 directly.

        yt-dlp + ffmpeg must be installed on the worker. We import
        lazily so the rest of the service still works on slimmed
        deployments that excluded them."""
        try:
            import yt_dlp                                              # noqa: PLC0415
        except ImportError as exc:
            raise MediaImportError(
                "yt-dlp not installed. `pip install yt-dlp` + add ffmpeg.",
            ) from exc

        tmpdir = Path(tempfile.mkdtemp(prefix="smms-import-"))
        try:
            ydl_opts = {
                "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
                "format": (
                    f"b[ext=mp4][height<={max_height}][filesize<100M]/"
                    f"bv*[ext=mp4][height<={max_height}]+ba[ext=m4a]/"
                    f"b[height<={max_height}]"
                ),
                "merge_output_format": "mp4",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "ratelimit": 5 * 1024 * 1024,
                "max_filesize": MAX_BYTES,
                "retries": 2,
            }

            def _do() -> dict:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    fp = ""
                    for d in info.get("requested_downloads", []) or []:
                        fp = d.get("filepath") or fp
                    if not fp:
                        fp = ydl.prepare_filename(info)
                    return {"filepath": fp, "title": info.get("title", "")}

            try:
                result = await asyncio.to_thread(_do)
            except Exception as exc:                                  # noqa: BLE001
                raise MediaImportError(f"yt-dlp failed: {exc}") from exc

            fp = Path(result["filepath"])
            if not fp.exists():
                raise MediaImportError(
                    "yt-dlp reported success but file is missing.",
                )

            size = fp.stat().st_size
            if size > MAX_BYTES:
                raise MediaImportError(
                    f"Downloaded video is {size // (1024*1024)} MB — over the "
                    f"{MAX_BYTES // (1024*1024)} MB ceiling. Try lower max_height.",
                )

            name = (
                _SAFE_NAME_RE.sub("_", title_hint or result["title"])[:60] + ".mp4"
            )
            data = fp.read_bytes()
            return await self.import_bytes(
                org_id=org_id, data=data,
                content_type="video/mp4",
                filename=name, source_url=url,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Helpers ─────────────────────────────────────────────────────────
def _filename_from_url(url: str) -> str | None:
    """Best-effort: pull the last path segment, strip the query."""
    try:
        tail = url.rsplit("/", 1)[-1].split("?", 1)[0]
        return tail or None
    except Exception:                                                 # noqa: BLE001
        return None


def _sniff_mime_from_name(name: str) -> str:
    n = name.lower()
    if n.endswith(".jpg") or n.endswith(".jpeg"):
        return "image/jpeg"
    if n.endswith(".png"):
        return "image/png"
    if n.endswith(".webp"):
        return "image/webp"
    if n.endswith(".gif"):
        return "image/gif"
    if n.endswith(".mp4"):
        return "video/mp4"
    if n.endswith(".mov"):
        return "video/quicktime"
    return "application/octet-stream"


def filter_allowed_urls(urls: Iterable[str]) -> list[str]:
    """Best-effort filter — drop obviously non-fetchable URLs (about:,
    data:, file:) before any import attempt. Used by source plugins."""
    out: list[str] = []
    for u in urls:
        if not u:
            continue
        if u.startswith(("http://", "https://")):
            out.append(u)
    return out
