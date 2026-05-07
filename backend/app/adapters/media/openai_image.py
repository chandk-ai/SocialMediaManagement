"""OpenAI DALL-E 3 image generation."""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin

from .base import MediaBrief, MediaGenerator

log = get_logger(__name__)

SIZES = {"1:1": "1024x1024", "16:9": "1792x1024", "9:16": "1024x1792"}


@register_plugin("media", "openai_image", api_version="1.0", category="image")
class OpenAIImageGenerator(MediaGenerator):
    display_name = "OpenAI DALL-E"
    description = "Generates images via OpenAI's images.generate endpoint."
    supported_kinds = (MediaKind.IMAGE,)
    config_schema = {
        "type": "object",
        "properties": {
            "model": {"type": "string", "default": "dall-e-3"},
            "quality": {"enum": ["standard", "hd"], "default": "standard"},
        },
    }

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self.api_key = os.getenv("LLM_OPENAI_API_KEY", "")
        self._client: Any | None = None
        if self.api_key:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self.api_key)
            except ImportError:
                log.warning("openai_sdk_missing_for_images")

    async def generate(self, brief: MediaBrief) -> MediaAsset:
        size = SIZES.get(brief.aspect_ratio, "1024x1024")
        if self._client is None:
            return _mock_image(brief, size)
        rsp = await self._client.images.generate(
            model=self.config.get("model", "dall-e-3"),
            prompt=_compose_prompt(brief),
            size=size,
            quality=self.config.get("quality", "standard"),
            n=1,
        )
        url = rsp.data[0].url
        return MediaAsset(url=url, kind=MediaKind.IMAGE,
                          alt_text=brief.prompt[:120],
                          width=int(size.split("x")[0]),
                          height=int(size.split("x")[1]))


def _compose_prompt(b: MediaBrief) -> str:
    bits = [b.prompt]
    if b.style: bits.append(f"Style: {b.style}.")
    return " ".join(bits)


def _mock_image(brief: MediaBrief, size: str) -> MediaAsset:
    w, h = (int(x) for x in size.split("x"))
    seed = abs(hash(brief.prompt)) % 1000
    return MediaAsset(
        url=f"https://picsum.photos/seed/{seed}/{w}/{h}",
        kind=MediaKind.IMAGE, alt_text=brief.prompt[:120],
        width=w, height=h,
    )
