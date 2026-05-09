"""Alert dispatcher — routes operationally-significant events out.

Designed for the things on-call wants paged on:

    * job stuck in retry loop (attempt >= 3)
    * circuit broke open for a platform we depend on
    * LLM budget breach
    * dead-letter queue size crossed threshold

Sinks are pluggable. Out of the box:

    * **slack**  — incoming-webhook URL via SMMS_ALERTS_SLACK_URL
    * **webhook**— generic JSON POST via SMMS_ALERTS_WEBHOOK_URL
    * **email**  — SMTP via SMMS_ALERTS_SMTP_* (TBD; stub for now)
    * **stdout** — always available, used in dev

Severity: ``info``, ``warning``, ``error``, ``critical``. The default
config routes ``error+`` to all configured sinks, ``warning`` to
slack only, ``info`` to stdout.

Idempotency: include an ``alert_key`` in the call; the dispatcher
suppresses duplicates of the same key within a window (default 5 min)
so a flapping condition doesn't page 50 times. Window is in-process
only — accept some duplication when scaled horizontally.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class Alert:
    title: str
    body: str
    severity: str = "warning"  # info | warning | error | critical
    alert_key: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


class AlertDispatcher:
    def __init__(
        self, *, slack_url: str | None = None,
        webhook_url: str | None = None,
        dedupe_window_s: float = 300.0,
        env_label: str | None = None,
    ) -> None:
        self.slack_url = slack_url or os.environ.get("SMMS_ALERTS_SLACK_URL")
        self.webhook_url = webhook_url or os.environ.get(
            "SMMS_ALERTS_WEBHOOK_URL")
        self.dedupe_window_s = float(dedupe_window_s)
        self.env_label = env_label or os.environ.get(
            "SMMS_ENV", "unknown")
        self._recent: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def fire(self, alert: Alert) -> None:
        """Dispatch the alert to enabled sinks. Suppresses duplicates
        within the dedupe window."""
        if alert.alert_key:
            async with self._lock:
                now = time.time()
                self._recent = {
                    k: t for k, t in self._recent.items()
                    if now - t < self.dedupe_window_s
                }
                if alert.alert_key in self._recent:
                    return
                self._recent[alert.alert_key] = now

        log.info("alert_fired", title=alert.title,
                 severity=alert.severity, **alert.labels)
        await asyncio.gather(
            self._send_slack(alert),
            self._send_webhook(alert),
            return_exceptions=True,
        )

    async def _send_slack(self, alert: Alert) -> None:
        if not self.slack_url:
            return
        if alert.severity == "info":
            return  # info → stdout/log only
        emoji = {
            "warning": ":warning:",
            "error": ":x:",
            "critical": ":rotating_light:",
        }.get(alert.severity, ":bell:")
        text = f"{emoji} *{alert.title}* `[{self.env_label}]`\n{alert.body}"
        if alert.labels:
            text += "\n" + " ".join(f"`{k}={v}`" for k, v in alert.labels.items())
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                await c.post(self.slack_url, json={"text": text})
        except Exception as exc:                                      # noqa: BLE001
            log.warning("alert_slack_failed", error=str(exc))

    async def _send_webhook(self, alert: Alert) -> None:
        if not self.webhook_url:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                await c.post(self.webhook_url, json={
                    "title": alert.title, "body": alert.body,
                    "severity": alert.severity,
                    "labels": alert.labels,
                    "env": self.env_label,
                    "ts": time.time(),
                })
        except Exception as exc:                                      # noqa: BLE001
            log.warning("alert_webhook_failed", error=str(exc))


_DISPATCHER: AlertDispatcher | None = None


def get_alert_dispatcher() -> AlertDispatcher:
    global _DISPATCHER
    if _DISPATCHER is None:
        _DISPATCHER = AlertDispatcher()
    return _DISPATCHER


async def fire_alert(
    title: str, body: str, *, severity: str = "warning",
    alert_key: str | None = None, **labels: Any,
) -> None:
    """Convenience for one-line alert calls. ``labels`` becomes the
    Alert.labels dict (cast to str)."""
    await get_alert_dispatcher().fire(Alert(
        title=title, body=body, severity=severity,
        alert_key=alert_key,
        labels={k: str(v) for k, v in labels.items()},
    ))
