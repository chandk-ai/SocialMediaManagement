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

    async def update(
        self, *, org_id: OrgId, trigger_id: TriggerId,
        display_name: str | None = None,
        config: dict | None = None,
        allowed_senders: list[str] | None = None,
        review_channel: str | None = None,
        review_recipient: str | None = None,
        is_active: bool | None = None,
    ) -> Trigger | None:
        """Partial update. Caller is responsible for org-scoping —
        we re-fetch via the org-scoped ``repo.get`` to enforce it
        defensively. Returns the updated trigger or ``None`` if not found.

        Note: ``plugin_name`` is intentionally not editable. Changing
        the underlying plugin would invalidate the config schema. The
        delete + recreate path is the supported migration.
        """
        t = await self.repo.get(org_id, trigger_id)
        if t is None:
            return None
        # Validate the (possibly new) review channel before persisting —
        # better to 422 here than fail at the next workflow run.
        if review_channel is not None and review_channel != "":
            self.registry.get(PluginKind.REVIEW_CHANNEL, review_channel)
        if display_name is not None:
            t.display_name = display_name
        if config is not None:
            t.config = dict(config)            # full replacement (see schema docstring)
        if allowed_senders is not None:
            t.allowed_senders = list(allowed_senders)
        if review_channel is not None:
            # Empty string clears it; non-empty sets it.
            t.review_channel = review_channel or None
        if review_recipient is not None:
            t.review_recipient = review_recipient or None
        if is_active is not None:
            t.is_active = bool(is_active)
        return await self.repo.update(t)

    async def delete(self, org_id: OrgId, trigger_id: TriggerId) -> bool:
        """Returns True if the trigger existed and was deleted, False
        if it didn't exist. The org-scoped ``repo.get`` guards against
        cross-tenant deletes (we never touch a trigger from another
        org's row)."""
        t = await self.repo.get(org_id, trigger_id)
        if t is None:
            return False
        await self.repo.delete(org_id, trigger_id)
        return True

    # ── inbound webhook dispatch ─────────────────────────────────────
    def adapter_for(self, trigger: Trigger) -> TriggerAdapter:
        entry = self.registry.get(PluginKind.TRIGGER, trigger.plugin_name)
        # Inject the trigger's org_id into the adapter config under the
        # ``__org_id__`` convention. Adapters that need to call
        # MediaImportService / Supabase Storage / any other org-scoped
        # service (e.g. Telegram downloading attached photos) read this
        # to write into the right tenant's storage path. Without it the
        # adapter defaults to a literal "shared" prefix that the bucket
        # policy rejects → user-attached media silently lost. The same
        # convention is used by source plugins (Notion, Drive, etc.).
        config = dict(trigger.config or {})
        config["__org_id__"] = str(trigger.org_id)
        return entry.cls(config=config)

    async def parse_payload(
        self, trigger: Trigger, payload: dict[str, Any], headers: dict[str, str], body: bytes,
    ) -> list[TriggerEvent]:
        # Honor the pause flag — when a trigger is paused, ACK the
        # inbound webhook (so the upstream provider doesn't retry) but
        # don't dispatch any events. The audit trail captures the
        # paused-skip via the trigger_paused_skip log line.
        if not trigger.is_active:
            log.info("trigger_paused_skip",
                     trigger_id=str(trigger.id),
                     plugin=trigger.plugin_name)
            return []
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
