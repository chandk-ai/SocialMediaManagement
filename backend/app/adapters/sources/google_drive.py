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
    description = "Reads docs/PDF/text files from a Google Drive folder."
    config_schema = {
        "type": "object",
        "required": ["folder_id"],
        "properties": {
            "folder_id": {"type": "string", "title": "Drive folder ID"},
            "service_account_json_path": {"type": "string",
                                          "title": "Path to service-account JSON key"},
            "mime_types": {"type": "array", "items": {"type": "string"},
                           "default": [
                               "application/vnd.google-apps.document",
                               "application/pdf", "text/plain", "text/markdown",
                           ]},
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
        except ImportError:
            log.info("google_drive_sdk_missing — falling back to stub")
            return
        key_path = self.config.get("service_account_json_path")
        if not key_path:
            raise SourceConnectionError("service_account_json_path is required")
        creds = service_account.Credentials.from_service_account_file(
            key_path, scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        if self._service is None:
            yield SourceItem(
                external_id="stub-1", title="(Google Drive stub)",
                body="Connect google-api-python-client and provide credentials.",
                url=None, published_at=datetime.utcnow(),
            )
            return
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
