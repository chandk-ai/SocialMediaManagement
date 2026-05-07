"""Trigger adapter contract.

A `TriggerAdapter` translates an external event (a webhook payload, an inbound
WhatsApp message, a cron tick) into a `TriggerEvent` — the canonical input
shape that the orchestration service understands.

Each `Trigger` row in the DB binds a workflow + adapter + per-trigger config
(secret, allowed senders, default review recipient).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar


class TriggerError(Exception):
    """Raised when a trigger event can't be parsed or fails verification."""


@dataclass(frozen=True, slots=True)
class TriggerEvent:
    """Canonical, channel-agnostic representation of an inbound trigger.

    The orchestrator turns this into a `WorkflowRun` whose `directive` field
    is fed to the Planner agent so the run is shaped by the user's request.
    """
    trigger_id: str                # ID of the configured Trigger row
    sender: str                    # phone / IG handle / email — used for allowlist + reply-to
    directive: str                 # the user's free-text instruction (or "")
    media_urls: list[str] = field(default_factory=list)
    occurred_at: datetime = field(default_factory=datetime.utcnow)
    raw: dict[str, Any] = field(default_factory=dict)
    # When the channel sends back a reply that the user expects threaded
    # (e.g. WhatsApp button replies), `in_reply_to` carries the original
    # outbound message id so we can correlate to a ReviewSession.
    in_reply_to: str | None = None


class TriggerAdapter(ABC):
    plugin_name: ClassVar[str] = ""
    api_version: ClassVar[str] = "1.0"
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    config_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    async def parse(self, payload: dict[str, Any]) -> list[TriggerEvent]:
        """Extract zero or more `TriggerEvent`s from an inbound webhook payload.

        Implementations must verify the payload's authenticity
        (HMAC signature, shared-secret token, OAuth scope, ...).
        Return [] when the payload is a valid health-check / handshake.
        """

    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        """Optional override — invoked by the webhook router before parse()."""
        return True
