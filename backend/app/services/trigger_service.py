"""Trigger service — manages Trigger rows and dispatches inbound events
to the workflow service."""
from __future__ import annotations

from typing import Any

from app.adapters.triggers.base import TriggerAdapter, TriggerEvent
from app.core.logging import get_logger
from app.domain.entities.trigger import Trigger, TriggerKind
from app.domain.value_objects.ids import OrgId, TriggerId, WorkflowId
from app.plugins.registry import PluginKind, PluginRegistry
from app.repositories.ports import TriggerRepository

log = get_logger(__name__)


class TriggerService:
    def __init__(self, repo: TriggerRepository, registry: PluginRegistry) -> None:
        self.repo = repo
        self.registry = registry

    # ── CRUD ──────────────────────────────────────────────────────────
    async def create(
        self, *, org_id: OrgId, workflow_id: WorkflowId, plugin_name: str,
        display_name: str, kind: TriggerKind, config: dict | None = None,
        allowed_senders: list[str] | None = None,
        review_channel: str | None = None,
        review_recipient: str | None = None,
    ) -> Trigger:
        # Validate that the plugin is registered.
        self.registry.get(PluginKind.TRIGGER, plugin_name)
        if review_channel:
            self.registry.get(PluginKind.REVIEW_CHANNEL, review_channel)
        t = Trigger.create(
            org_id=org_id, workflow_id=workflow_id, plugin_name=plugin_name,
            display_name=display_name, kind=kind, config=config,
            allowed_senders=allowed_senders, review_channel=review_channel,
            review_recipient=review_recipient,
        )
        return await self.repo.add(t)

    async def list(self, org_id: OrgId) -> list[Trigger]:
        return await self.repo.list(org_id)

    async def get(self, org_id: OrgId, trigger_id: TriggerId) -> Trigger | None:
        return await self.repo.get(org_id, trigger_id)

    async def get_any(self, trigger_id: TriggerId) -> Trigger | None:
        return await self.repo.get_any(trigger_id)

    # ── inbound webhook dispatch ─────────────────────────────────────
    def adapter_for(self, trigger: Trigger) -> TriggerAdapter:
        entry = self.registry.get(PluginKind.TRIGGER, trigger.plugin_name)
        return entry.cls(config=trigger.config)

    async def parse_payload(
        self, trigger: Trigger, payload: dict[str, Any], headers: dict[str, str], body: bytes,
    ) -> list[TriggerEvent]:
        adapter = self.adapter_for(trigger)
        if not await adapter.verify_signature(headers, body):
            log.warning("trigger_signature_invalid", trigger_id=str(trigger.id))
            return []
        # Stamp trigger_id so downstream WorkflowService knows the routing.
        payload = dict(payload, trigger_id=str(trigger.id))
        events = await adapter.parse(payload)
        # Enforce the allowlist.
        if trigger.allowed_senders:
            events = [e for e in events if e.sender in trigger.allowed_senders]
        return events
