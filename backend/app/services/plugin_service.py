"""Read-side service: lists installed plugins for the UI."""
from __future__ import annotations

from app.adapters.platforms.base import PlatformCapabilities, SocialPlatform
from app.adapters.sources.base import ContentSource
from app.adapters.llm.base import LLMProvider
from app.plugins.registry import PluginKind, PluginRegistry
from app.schemas.plugins import PluginInfo


class PluginService:
    def __init__(self, registry: PluginRegistry) -> None:
        self.registry = registry

    def list(self, kind: PluginKind | None = None) -> list[PluginInfo]:
        out: list[PluginInfo] = []
        for entry in self.registry.list(kind):
            cls = entry.cls
            info = PluginInfo(
                name=entry.name,
                kind=entry.kind.value,
                display_name=getattr(cls, "display_name", entry.name),
                api_version=entry.api_version,
                description=getattr(cls, "description", ""),
                metadata=entry.metadata,
                config_schema=getattr(cls, "config_schema", {}),
                capabilities=_capabilities(cls),
            )
            out.append(info)
        return out


def _capabilities(cls: type) -> dict:
    if issubclass(cls, SocialPlatform):
        caps: PlatformCapabilities = getattr(cls, "capabilities", PlatformCapabilities())
        return {
            "kind": "platform",
            "max_text_length": cls.max_text_length,
            "max_hashtags": cls.max_hashtags,
            **{k: getattr(caps, k) for k in (
                "text_only", "image", "video", "gif", "document",
                "threads", "scheduling", "analytics",
            )},
        }
    if issubclass(cls, ContentSource):
        return {"kind": "source"}
    if issubclass(cls, LLMProvider):
        return {"kind": "llm", "default_model": getattr(cls, "default_model", "")}
    return {}
