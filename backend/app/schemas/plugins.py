from __future__ import annotations

from .common import APIModel


class PluginInfo(APIModel):
    name: str
    kind: str
    display_name: str
    api_version: str
    description: str = ""
    metadata: dict = {}
    config_schema: dict = {}
    capabilities: dict = {}
