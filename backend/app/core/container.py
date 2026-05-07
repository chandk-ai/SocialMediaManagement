"""Dependency-injection container — wires every component without globals.

Adapters/services that want a dependency just declare it in __init__; the
container supplies it. Tests can override providers per test case.
"""
from __future__ import annotations

from dependency_injector import containers, providers

from app.core.config import Settings, get_settings
from app.core.secrets import build_token_vault
from app.plugins.registry import PluginRegistry


class Container(containers.DeclarativeContainer):
    """Composition root."""

    wiring_config = containers.WiringConfiguration(
        packages=[
            "app.api",
            "app.services",
            "app.agents",
            "app.workers",
        ]
    )

    settings: providers.Singleton[Settings] = providers.Singleton(get_settings)

    # ──────────────────────────────────────────────── infrastructure
    token_vault = providers.Singleton(build_token_vault, settings=settings)
    plugin_registry: providers.Singleton[PluginRegistry] = providers.Singleton(PluginRegistry)

    # Database / repositories / queues are wired in `infrastructure/wire.py`
    # to keep this file dependency-light during static analysis.


def build_container() -> Container:
    c = Container()
    return c
