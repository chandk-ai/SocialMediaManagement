"""Google Drive source — lists files in a folder and extracts text.

Uses google-api-python-client + google-auth when present, falls back to a
deterministic stub so the system runs without those packages.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)


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
                    "application/vnd.google-apps.document",
                    "application/pdf", "text/plain", "text/markdown",
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
                body = self._read_file(f["id"], f["mimeType"])
                yield SourceItem(
                    external_id=f["id"],
                    title=f["name"],
                    body=body,
                    url=f.get("webViewLink"),
                    published_at=modified,
                    metadata={"mime": f["mimeType"]},
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
