"""Email review channel — sends a plain-text email with reply-to threading.

Default: SMTP via aiosmtplib if env vars are set; otherwise dry-run.
"""
from __future__ import annotations

import os

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import ReviewChannel, ReviewMessage

log = get_logger(__name__)


@register_plugin("review_channel", "email", api_version="1.0", category="messaging")
class EmailReviewChannel(ReviewChannel):
    display_name = "Email"
    description = "Emails the reviewer; their reply (parsed via inbound IMAP/Mailgun) is the decision."

    config_schema = {
        "type": "object",
        "properties": {
            "smtp_host":   {"type": "string", "default": "smtp.sendgrid.net"},
            "smtp_port":   {"type": "integer", "default": 587},
            "from_addr":   {"type": "string"},
            "subject":     {"type": "string", "default": "[SMMS] Approve this post?"},
        },
    }

    async def send_for_review(self, recipient: str, message: ReviewMessage) -> str:
        host = self.config.get("smtp_host", "smtp.sendgrid.net")
        port = int(self.config.get("smtp_port", 587))
        sender = self.config.get("from_addr") or os.getenv("SMMS_FROM_EMAIL", "")
        subject = self.config.get("subject", "[SMMS] Approve this post?")
        user = os.getenv("SMTP_USER", "")
        pwd  = os.getenv("SMTP_PASSWORD", "")

        body = self._render(message)
        if not (sender and user and pwd):
            log.info("email_review_dry_run", recipient=recipient, drafts=len(message.drafts))
            return f"dry-run:{recipient}"
        try:
            import aiosmtplib                              # type: ignore
            from email.message import EmailMessage
            em = EmailMessage()
            em["From"] = sender
            em["To"] = recipient
            em["Subject"] = subject
            em["Reply-To"] = sender
            em.set_content(body)
            await aiosmtplib.send(em, hostname=host, port=port,
                                  username=user, password=pwd, start_tls=True)
            return em["Message-ID"] or f"email:{recipient}"
        except Exception as exc:                           # noqa: BLE001
            log.warning("email_send_failed", error=str(exc))
            return f"failed:{recipient}"

    @staticmethod
    def _render(message: ReviewMessage) -> str:
        parts = [message.headline, ""]
        for d in message.drafts:
            parts.append(f"--- {d.get('platform_name','')} ---")
            parts.append(d.get("text", ""))
            parts.append("")
        parts.append("Reply: APPROVE  |  REVISE: <feedback>  |  REJECT")
        return "\n".join(parts)
