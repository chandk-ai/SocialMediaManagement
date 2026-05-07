"""Supabase Realtime — broadcast workflow run progress to the frontend.

Two patterns:
1. **Postgres-changes**: clients subscribe to `workflow_runs` row updates and
   the agent trace appears live (no extra code on the backend — just write to
   the table).
2. **Broadcast** channel for ephemeral events (agent thinking ticks). We
   publish from the worker via this adapter.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class SupabaseRealtime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.channel = self.settings.supabase.realtime_channel

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        """Publish an ephemeral broadcast event to the project channel."""
        url = self.settings.supabase.url
        key = self.settings.supabase.service_role_key.get_secret_value()
        if not url or not key:
            log.info("realtime_skipped_not_configured", event=event)
            return
        body = {
            "topic": f"realtime:{self.channel}",
            "event": event,
            "payload": payload,
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{url}/realtime/v1/broadcast",
                    headers={"apikey": key, "Authorization": f"Bearer {key}"},
                    content=json.dumps(body),
                )
        except httpx.HTTPError as exc:
            log.warning("realtime_broadcast_failed", event=event, error=str(exc))
