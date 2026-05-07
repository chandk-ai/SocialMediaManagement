"""Amazon S3 / MinIO / any S3-compatible bucket source."""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError


@register_plugin("source", "s3", api_version="1.0")
class S3Source(ContentSource):
    display_name = "S3 bucket"
    description = "Lists objects in an S3 (or MinIO / R2 / Wasabi) bucket and reads text-like ones."
    config_schema = {
        "type": "object",
        "required": ["bucket"],
        "properties": {
            "bucket":            {"type": "string", "title": "Bucket name"},
            "prefix":            {"type": "string", "default": "",
                                  "title": "Object key prefix",
                                  "description": "Only list objects under this prefix"},
            "endpoint_url":      {"type": "string", "title": "Custom endpoint (MinIO/R2 etc.)"},
            "region":            {"type": "string", "default": "us-east-1"},
            "access_key_id":     {"type": "string", "title": "Access Key ID (optional)"},
            "secret_access_key": {"type": "string", "format": "password",
                                  "title": "Secret Access Key (optional)"},
            "extensions": {"type": "array", "items": {"type": "string"},
                           "default": [".md", ".txt", ".json", ".html"],
                           "title": "File extensions to include"},
            "max_keys":  {"type": "integer", "default": 200, "minimum": 1, "maximum": 1000,
                          "title": "Max objects per fetch"},
        },
    }

    def _client(self):
        try:
            import boto3
        except ImportError as exc:                              # pragma: no cover
            raise SourceConnectionError(
                "boto3 is not installed on the backend; cannot connect to S3.",
            ) from exc
        kwargs: dict[str, Any] = {"region_name": self.config.get("region", "us-east-1")}
        if self.config.get("endpoint_url"):
            kwargs["endpoint_url"] = self.config["endpoint_url"]
        if self.config.get("access_key_id"):
            kwargs["aws_access_key_id"] = self.config["access_key_id"]
        if self.config.get("secret_access_key"):
            kwargs["aws_secret_access_key"] = self.config["secret_access_key"]
        return boto3.client("s3", **kwargs)

    async def connect(self) -> None:
        try:
            client = self._client()
            # head_bucket validates both reachability and credentials.
            client.head_bucket(Bucket=self.config["bucket"])
        except SourceConnectionError:
            raise
        except Exception as exc:                                 # noqa: BLE001
            raise SourceConnectionError(
                f"Could not reach bucket {self.config.get('bucket')!r}: {exc}",
            ) from exc

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        s3 = self._client()
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
