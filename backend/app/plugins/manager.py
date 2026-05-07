"""Plugin manager — boots the registry at app startup."""
from __future__ import annotations

import importlib
import pkgutil

from app.core.logging import get_logger
from app.plugins.registry import (
    PluginRegistry,
    discover_entry_point_plugins,
    get_global_registry,
)

log = get_logger(__name__)


class PluginManager:
    def __init__(self, registry: PluginRegistry | None = None) -> None:
        self.registry = registry or get_global_registry()

    def load_all(self) -> PluginRegistry:
        for pkg in (
            "app.adapters.platforms",
            "app.adapters.sources",
            "app.adapters.llm",
            "app.adapters.triggers",
            "app.adapters.review_channels",
            "app.adapters.media",
            "app.adapters.engagement",
        ):
            try:
                self._load_built_in(pkg)
            except ModuleNotFoundError:
                continue
        for entry in discover_entry_point_plugins():
            self.registry.register(entry)
        log.info("plugins_loaded", count=len(self.registry.list()))
        return self.registry

    def _load_built_in(self, package_name: str) -> None:
        package = importlib.import_module(package_name)
        for _, modname, _ in pkgutil.iter_modules(package.__path__):
            if modname.startswith("_") or modname == "base":
                continue
            importlib.import_module(f"{package_name}.{modname}")
