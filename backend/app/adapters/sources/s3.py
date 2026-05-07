"""Amazon S3 / MinIO / any S3-compatible bucket source."""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource


@register_plugin("source", "s3", api_version="1.0")
class S3Source(ContentSource):
    display_name = "S3 bucket"
    description = "Lists objects in an S3 (or MinIO / R2 / Wasabi) bucket and reads text-like ones."
    config_schema = {
        "type": "object",
        "required": ["bucket"],
        "properties": {
            "bucket":    {"type": "string", "title": "Bucket name"},
            "prefix":    {"type": "string", "default": ""},
            "endpoint_url": {"type": "string", "title": "Custom endpoint (MinIO etc.)"},
            "region":    {"type": "string", "default": "us-east-1"},
            "extensions": {"type": "array", "items": {"type": "string"},
                           "default": [".md", ".txt", ".json", ".html"]},
            "max_keys":  {"type": "integer", "default": 200},
        },
    }

    async def connect(self) -> None:
        return None

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        try:
            import boto3
        except ImportError:
            yield SourceItem(
                external_id="stub-1", title="(S3 stub)",
                body="Install boto3 to read from a real bucket.",
                url=None, published_at=datetime.utcnow(),
            )
            return
        kwargs: dict[str, Any] = {"region_name": self.config.get("region", "us-east-1")}
        if self.config.get("endpoint_url"):
            kwargs["endpoint_url"] = self.config["endpoint_url"]
        s3 = boto3.client("s3", **kwargs)
        exts = tuple(self.config.get("extensions", [".md", ".txt"]))
        token = None
        seen = 0
        max_keys = int(self.config.get("max_keys", 200))
        while seen < max_keys:
            params: dict[str, Any] = {
                "Bucket": self.config["bucket"],
                "Prefix": self.config.get("prefix", ""),
                "MaxKeys": min(1000, max_keys - seen),
            }
            if token:
                params["ContinuationToken"] = token
            resp = s3.list_objects_v2(**params)
            for obj in resp.get("Contents", []):
                if not obj["Key"].endswith(exts):
                    continue
                if since and obj["LastModified"] <= since:
                    continue
                body = s3.get_object(Bucket=self.config["bucket"], Key=obj["Key"])["Body"].read()
                yield SourceItem(
                    external_id=obj["Key"],
                    title=obj["Key"].rsplit("/", 1)[-1],
                    body=body.decode("utf-8", errors="replace"),
                    url=None,
                    published_at=obj["LastModified"],
                )
                seen += 1
                if seen >= max_keys:
                    break
            token = resp.get("NextContinuationToken")
            if not token:
                break
