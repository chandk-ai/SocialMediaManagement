"""Deterministic mock — used as default so IG-required-media platforms
don't fail in dev / tests."""
from __future__ import annotations

from app.domain.value_objects.content import MediaAsset, MediaKind
from app.plugins.registry import register_plugin

from .base import MediaBrief, MediaGenerator


@register_plugin("media", "mock", api_version="1.0", category="builtin")
class MockMediaGenerator(MediaGenerator):
    display_name = "Mock media generator"
    description = "Returns a deterministic placeholder image / video URL."
    supported_kinds = (MediaKind.IMAGE, MediaKind.VIDEO, MediaKind.GIF)

    async def generate(self, brief: MediaBrief) -> MediaAsset:
        seed = abs(hash(brief.prompt)) % 10000
        if brief.kind is MediaKind.VIDEO:
            url = f"https://example.com/mock-video/{seed}.mp4"
            return MediaAsset(url=url, kind=MediaKind.VIDEO,
                              alt_text=brief.prompt[:120], duration_sec=15)
        url = f"https://picsum.photos/seed/{seed}/1024/1024"
        return MediaAsset(url=url, kind=brief.kind, alt_text=brief.prompt[:120],
                          width=1024, height=1024)
