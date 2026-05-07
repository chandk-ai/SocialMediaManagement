"""Trigger — the *entry points* into the agent pipeline.

A workflow can be initiated by:
* a manual click in the UI                       (`manual`)
* a schedule (cron / interval)                   (`schedule`)
* a generic webhook (any inbound HTTP)           (`webhook`)
* an inbound WhatsApp message                    (`whatsapp`)
* an inbound Instagram DM / mention              (`instagram`)
* email, SMS, Slack slash-command, ... (future)

Each Trigger row binds a `plugin_name` to a workflow and stores the
plugin-specific config (phone numbers, webhook secret, allowed senders).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..value_objects.ids import OrgId, TriggerId, WorkflowId, new_id


class TriggerKind(str, Enum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    TELEGRAM = "telegram"
    EMAIL = "email"
    SLACK = "slack"


@dataclass(slots=True)
class Trigger:
    id: TriggerId
    org_id: OrgId
    workflow_id: WorkflowId
    plugin_name: str               # references a TriggerAdapter in the registry
    display_name: str
    kind: TriggerKind
    config: dict = field(default_factory=dict)
    is_active: bool = True
    # An allowlist of senders (phone numbers, IG account IDs, emails) that
    # can fire this trigger. Empty list = anyone the channel routes to us.
    allowed_senders: list[str] = field(default_factory=list)
    # Default review channel for runs initiated by this trigger.
    review_channel: str | None = None     # e.g. "whatsapp", "in_app"
    review_recipient: str | None = None   # phone / IG id / email of approver
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_fired_at: datetime | None = None

    @classmethod
    def create(
        cls, *, org_id: OrgId, workflow_id: WorkflowId, plugin_name: str,
        display_name: str, kind: TriggerKind, config: dict | None = None,
        allowed_senders: list[str] | None = None,
        review_channel: str | None = None,
        review_recipient: str | None = None,
    ) -> "Trigger":
        return cls(
            id=TriggerId(new_id()),
            org_id=org_id,
            workflow_id=workflow_id,
            plugin_name=plugin_name,
            display_name=display_name,
            kind=kind,
            config=config or {},
            allowed_senders=allowed_senders or [],
            review_channel=review_channel,
            review_recipient=review_recipient,
        )
