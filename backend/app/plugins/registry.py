"""Runtime registry of plugins (platforms, sources, LLMs).

Plugins self-register via the @register_plugin decorator. The registry is
exposed through the DI container so any service can list/lookup plugins
without static imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from app.domain.exceptions import PluginIncompatibleError, PluginNotRegisteredError

SUPPORTED_API_RANGE = (1, 2)   # >=1.0, <2.0


class PluginKind(str, Enum):
    PLATFORM = "platform"
    SOURCE = "source"
    LLM = "llm"
    TRIGGER = "trigger"
    REVIEW_CHANNEL = "review_channel"
    MEDIA = "media"
    ENGAGEMENT = "engagement"
    # Item-selection strategies — pluggable layer that decides which
    # SourceItems to feed to the agents and in what mode (synthesize vs
    # one-post-per-item). See app/services/selection/base.py.
    SELECTION = "selection"


@dataclass
class PluginEntry:
    name: str
    kind: PluginKind
    cls: type
    api_version: str = "1.0"
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[PluginKind, str], PluginEntry] = {}

    def register(self, entry: PluginEntry) -> None:
        major = int(entry.api_version.split(".")[0])
        if not (SUPPORTED_API_RANGE[0] <= major < SUPPORTED_API_RANGE[1]):
            raise PluginIncompatibleError(
                f"Plugin {entry.name!r} api_version {entry.api_version} unsupported"
            )
        self._entries[(entry.kind, entry.name)] = entry

    def get(self, kind: PluginKind, name: str) -> PluginEntry:
        try:
            return self._entries[(kind, name)]
        except KeyError as exc:
            raise PluginNotRegisteredError(f"{kind.value}:{name}") from exc

    def list(self, kind: PluginKind | None = None) -> list[PluginEntry]:
        if kind is None:
            return list(self._entries.values())
        return [e for k, e in self._entries.items() if k[0] is kind]

    def __contains__(self, key: tuple[PluginKind, str]) -> bool:
        return key in self._entries


# Module-level instance used by the @register_plugin decorator. The DI
# container exposes the same instance so the rest of the app sees one source
# of truth.
_global_registry = PluginRegistry()


def get_global_registry() -> PluginRegistry:
    return _global_registry


def register_plugin(kind: str | PluginKind, name: str, *, api_version: str = "1.0", **metadata):
    """Class decorator that adds the plugin to the global registry on import."""
    kind_enum = PluginKind(kind) if isinstance(kind, str) else kind

    def _wrap(cls: type) -> type:
        cls.plugin_name = name           # type: ignore[attr-defined]
        cls.plugin_kind = kind_enum      # type: ignore[attr-defined]
        cls.api_version = api_version    # type: ignore[attr-defined]
        _global_registry.register(PluginEntry(
            name=name, kind=kind_enum, cls=cls,
            api_version=api_version, metadata=metadata,
        ))
        return cls

    return _wrap


def discover_entry_point_plugins() -> Iterable[PluginEntry]:
    """Find third-party plugins exposed via the `smms.plugins` entry-point group."""
    try:
        from importlib.metadata import entry_points
    except ImportError:                  # pragma: no cover
        return []
    eps = entry_points()
    group = getattr(eps, "select", lambda **kw: [])(group="smms.plugins")
    found: list[PluginEntry] = []
    for ep in group:
        cls = ep.load()
        kind = PluginKind(getattr(cls, "plugin_kind", PluginKind.PLATFORM))
        found.append(PluginEntry(
            name=getattr(cls, "plugin_name", ep.name),
            kind=kind,
            cls=cls,
            api_version=getattr(cls, "api_version", "1.0"),
        ))
    return found
