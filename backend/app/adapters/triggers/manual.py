"""Manual trigger — fired by a user clicking "Run now" in the UI or
calling `POST /workflows/{id}/run` directly.

Mostly a marker; included so the trigger plugin list is uniform and the UI
renders a row even for the default path.
"""
from __future__ import annotations

from typing import Any

from app.plugins.registry import register_plugin

from .base import TriggerAdapter, TriggerEvent


@register_plugin("trigger", "manual", api_version="1.0", category="builtin")
class ManualTrigger(TriggerAdapter):
    display_name = "Manual (UI / API)"
    description = "Fired explicitly from the dashboard or REST API."

    async def parse(self, payload: dict[str, Any]) -> list[TriggerEvent]:
        return [TriggerEvent(
            trigger_id=payload.get("trigger_id", ""),
            sender=payload.get("user", "ui"),
            directive=payload.get("directive", ""),
            raw=payload,
        )]
