"""File / blob storage source — reads markdown or text from a mounted folder
or S3 bucket. Default implementation uses local FS so the system works
out of the box."""
from __future__ import annotations

import os
from datetime import datetime
from typing import AsyncIterator

from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError


@register_plugin("source", "file", api_version="1.0")
class FileSource(ContentSource):
    display_name = "File folder"
    description = "Reads .md / .txt files from a directory."
    config_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "title": "Local folder path"},
            "extensions": {"type": "array", "items": {"type": "string"},
                           "default": [".md", ".txt"], "title": "Allowed extensions"},
        },
    }

    async def connect(self) -> None:
        path = self.config["path"]
        if not os.path.isdir(path):
            raise SourceConnectionError(f"Not a directory: {path}")

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        path = self.config["path"]
        exts = set(self.config.get("extensions", [".md", ".txt"]))
        for fname in sorted(os.listdir(path)):
            full = os.path.join(path, fname)
            if not os.path.isfile(full):
                continue
            if not any(fname.endswith(e) for e in exts):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(full))
            if since and mtime <= since:
                continue
            with open(full, "r", encoding="utf-8") as fh:
                body = fh.read()
            yield SourceItem(
                external_id=full,
                title=os.path.splitext(fname)[0].replace("_", " ").title(),
                body=body,
                url=None,
                published_at=mtime,
            )
