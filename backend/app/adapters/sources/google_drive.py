"""Google Drive source — lists files in a folder and extracts text.

Uses google-api-python-client + google-auth when present, falls back to a
deterministic stub so the system runs without those packages.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin
from app.services.media_import import MediaImportError, MediaImportService

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)

# Drive returns mimeTypes like "image/jpeg" / "video/mp4" — straight match.
# We treat anything else as a text-ish doc and let the existing _read_file
# path try to extract text from it.
_IMAGE_MIME_PREFIX = "image/"
_VIDEO_MIME_PREFIX = "video/"


@register_plugin("source", "google_drive", api_version="1.0")
class GoogleDriveSource(ContentSource):
    display_name = "Google Drive"
    description = "Reads docs/PDF/text files from a Google Drive folder using a service account."
    config_schema = {
        "type": "object",
        "required": ["folder_id"],
        "properties": {
            "folder_id": {
                "type": "string", "title": "Drive folder ID",
                "description": "Open the folder in Drive and copy the last URL segment.",
            },
            "service_account_json": {
                "type": "string", "format": "password",
                "title": "Service account JSON",
                "description": (
                    "Paste the entire JSON file contents from Google Cloud Console → "
                    "IAM → Service Accounts. The folder must be shared with the "
                    "service account's email."
                ),
            },
            "mime_types": {
                "type": "array", "items": {"type": "string"},
                "default": [
                    # Text-ish docs (extracted into SourceItem.body)
                    "application/vnd.google-apps.document",
                    "application/pdf", "text/plain", "text/markdown",
                    # Media (uploaded to Supabase Storage, attached to
                    # SourceItem.media). Drive supports these natively.
                    "image/jpeg", "image/png", "image/webp",
                    "video/mp4", "video/quicktime",
                ],
                "title": "MIME types to include",
            },
            "page_size": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
        },
    }

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._service: Any = None

    async def connect(self) -> None:
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:                                # pragma: no cover
            raise SourceConnectionError(
                "google-api-python-client is not installed on the backend.",
            ) from exc
        raw = self.config.get("service_account_json", "").strip()
        if not raw:
            raise SourceConnectionError(
                "Service account JSON is required. "
                "Create one in Google Cloud Console and share the Drive folder with its email.",
            )
        try:
            import json
            info = json.loads(raw)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/drive.readonly"],
            )
        except Exception as exc:                                  # noqa: BLE001
            raise SourceConnectionError(f"invalid service account JSON: {exc}") from exc
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        # Verify folder access early
        try:
            self._service.files().get(
                fileId=self.config["folder_id"], fields="id,name",
            ).execute()
        except Exception as exc:                                  # noqa: BLE001
            raise SourceConnectionError(
                f"Cannot access folder — share it with the service account email: {exc}",
            ) from exc

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        if self._service is None:
            await self.connect()
        folder = self.config["folder_id"]
        mimes = self.config.get("mime_types", [])
        q = f"'{folder}' in parents and trashed = false"
        if mimes:
            q += " and (" + " or ".join(f"mimeType='{m}'" for m in mimes) + ")"
        page_token = None
        while True:
            resp = self._service.files().list(
                q=q,
                pageSize=int(self.config.get("page_size", 100)),
                fields="nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink)",
                pageToken=page_token,
            ).execute()
            for f in resp.get("files", []):
                modified = datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00"))
                if since and modified <= since:
                    continue
                mime = f.get("mimeType") or ""
                metadata = {"mime": mime}

                if mime.startswith((_IMAGE_MIME_PREFIX, _VIDEO_MIME_PREFIX)):
                    # Binary asset — DON'T try to decode it as text; that
                    # produced garbled bodies in the May 2026 incident.
                    # Route through MediaImportService instead so the
                    # asset lands in Supabase Storage with a permanent
                    # public URL that platforms can fetch later.
                    media = await self._import_drive_media(
                        file_id=f["id"], mime=mime, name=f.get("name") or f["id"],
                    )
                    yield SourceItem(
                        external_id=f["id"],
                        title=f.get("name") or "",
                        body="",          # no text body for pure media
                        url=f.get("webViewLink"),
                        published_at=modified,
                        media=media,
                        metadata=metadata,
                    )
                    continue

                # Text-ish doc — original path. Failures here shouldn't
                # tank the iterator; surface as an empty body so the
                # planner can still see the file metadata.
                try:
                    body = self._read_file(f["id"], mime)
                except Exception as exc:                              # noqa: BLE001
                    log.warning("drive_read_file_failed",
                                file_id=f["id"], mime=mime, error=str(exc))
                    body = ""
                yield SourceItem(
                    external_id=f["id"],
                    title=f.get("name") or "",
                    body=body,
                    url=f.get("webViewLink"),
                    published_at=modified,
                    metadata=metadata,
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    def _read_file(self, file_id: str, mime: str) -> str:
        if mime == "application/vnd.google-apps.document":
            return self._service.files().export(
                fileId=file_id, mimeType="text/plain"
            ).execute().decode("utf-8")
        return self._service.files().get_media(fileId=file_id).execute().decode(
            "utf-8", errors="replace"
        )

    async def _import_drive_media(
        self, *, file_id: str, mime: str, name: str,
    ) -> tuple[MediaAsset, ...]:
        """Download a binary Drive file via the SDK (which signs the
        request with the service account) and push the bytes into
        Supabase Storage through ``MediaImportService``.

        We deliberately read via ``files().get_media`` rather than the
        sharing-link URL because the folder might not be world-readable
        — only the service account has access. The SDK call returns the
        raw bytes regardless of file ACLs.

        Returns a single-element tuple on success, empty on failure
        (logged). One bad file shouldn't abort the whole folder scan.
        """
        try:
            data = await asyncio.to_thread(
                lambda: self._service.files().get_media(fileId=file_id).execute()
            )
        except Exception as exc:                                      # noqa: BLE001
            log.warning("drive_media_download_failed",
                        file_id=file_id, error=str(exc))
            return ()

        if not isinstance(data, bytes):
            # The Drive SDK sometimes returns a decoded str for text
            # responses — for binary mimes this shouldn't happen, but
            # guard anyway.
            log.warning("drive_media_unexpected_type",
                        file_id=file_id, type=type(data).__name__)
            return ()

        importer = MediaImportService()
        org_id = str(self.config.get("__org_id__") or "shared")
        try:
            result = await importer.import_bytes(
                org_id=org_id, data=data,
                content_type=mime, filename=name,
            )
        except MediaImportError as exc:
            log.info("drive_media_import_skipped",
                     file_id=file_id, mime=mime, error=str(exc))
            return ()

        kind = MediaKind.VIDEO if mime.startswith(_VIDEO_MIME_PREFIX) else MediaKind.IMAGE
        return (MediaAsset(url=result.url, kind=kind, alt_text=name),)
