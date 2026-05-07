"""Schedule trigger — invoked by Celery Beat on the workflow's schedule."""
from __future__ import annotations

from typing import Any

from app.plugins.registry import register_plugin

from .base import TriggerAdapter, TriggerEvent


@register_plugin("trigger", "schedule", api_version="1.0", category="builtin")
class ScheduleTrigger(TriggerAdapter):
    display_name = "Schedule (cron / interval)"
    description = "Fires automatically based on the workflow's schedule."

    config_schema = {
        "type": "object",
        "properties": {
            "cron":             {"type": "string", "title": "Cron expression"},
            "interval_minutes": {"type": "integer", "minimum": 1},
            "timezone":         {"type": "string", "default": "UTC"},
        },
    }

    async def parse(self, payload: dict[str, Any]) -> list[TriggerEvent]:
        return [TriggerEvent(
            trigger_id=payload.get("trigger_id", ""),
            sender="scheduler",
            directive=payload.get("directive", ""),
            raw=payload,
        )]
