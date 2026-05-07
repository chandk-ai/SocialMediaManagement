"""Replicate — covers SDXL, Flux, and most open-weight image/video models."""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin

from .base import MediaBrief, MediaGenerator

log = get_logger(__name__)


@register_plugin("media", "replicate", api_version="1.0", category="image-video")
class ReplicateGenerator(MediaGenerator):
    display_name = "Replicate (SDXL / Flux / open models)"
    description = "Wraps any Replicate model via the predictions API."
    supported_kinds = (MediaKind.IMAGE, MediaKind.VIDEO)
    config_schema = {
        "type": "object",
        "required": ["model"],
        "properties": {
            "model": {"type": "string",
                      "examples": ["black-forest-labs/flux-schnell",
                                   "stability-ai/sdxl"]},
            "version": {"type": "string", "title": "Pinned model version (optional)"},
        },
    }

    async def generate(self, brief: MediaBrief) -> MediaAsset:
        token = os.getenv("REPLICATE_API_TOKEN", "")
        if not token:
            return _mock(brief)
        body: dict[str, Any] = {
            "input": {
                "prompt": brief.prompt,
                "aspect_ratio": brief.aspect_ratio,
                **brief.extras,
            },
        }
        version = self.config.get("version")
        if version:
            body["version"] = version
        else:
            body["model"] = self.config["model"]
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(
                    "https://api.replicate.com/v1/predictions",
                    json=body,
                    headers={"Authorization": f"Token {token}"},
                )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as exc:
            log.warning("replicate_call_failed", error=str(exc))
            return _mock(brief)
        url = (data.get("output") or [None])[0] or data.get("urls", {}).get("get", "")
        return MediaAsset(url=url, kind=brief.kind, alt_text=brief.prompt[:120])


def _mock(brief: MediaBrief) -> MediaAsset:
    seed = abs(hash(brief.prompt)) % 1000
    return MediaAsset(
        url=f"https://picsum.photos/seed/{seed}/1024/1024",
        kind=brief.kind, alt_text=brief.prompt[:120],
    )
