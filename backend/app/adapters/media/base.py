"""Media-generation plugin contract.

A `MediaGenerator` turns a `MediaBrief` (what the Planner asked for) into a
concrete `MediaAsset` the Executor can attach to a draft. The asset's URL
points at our storage bucket (Supabase Storage by default).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from app.domain.value_objects.content import MediaAsset, MediaKind


class MediaGenerationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class MediaBrief:
    """What the Planner / Executor want generated."""
    prompt: str
    kind: MediaKind = MediaKind.IMAGE
    aspect_ratio: str = "1:1"        # e.g. "1:1", "16:9", "9:16"
    style: str | None = None         # "photorealistic", "watercolor", ...
    duration_sec: float | None = None    # videos
    voice: str | None = None             # voice generators
    extras: dict = field(default_factory=dict)


class MediaGenerator(ABC):
    plugin_name: ClassVar[str] = ""
    api_version: ClassVar[str] = "1.0"
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    supported_kinds: ClassVar[tuple[MediaKind, ...]] = (MediaKind.IMAGE,)
    config_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    async def generate(self, brief: MediaBrief) -> MediaAsset: ...

    def supports(self, kind: MediaKind) -> bool:
        return kind in self.supported_kinds
