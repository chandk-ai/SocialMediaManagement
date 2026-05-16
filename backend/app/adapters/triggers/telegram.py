"""Telegram trigger — Bot API webhook.

Setup:
1. Create a bot via @BotFather → save the bot token.
2. POST a webhook URL:
       curl https://api.telegram.org/bot<TOKEN>/setWebhook \
            -d url=https://<your-host>/api/v1/webhooks/telegram/<trigger_id> \
            -d secret_token=<random>            # optional but recommended
3. Paste the same secret_token into this trigger's config — we verify the
   `X-Telegram-Bot-Api-Secret-Token` header on every inbound POST.

Inbound shapes we handle:
* `message`         — direct messages (text), with optional `reply_to_message`
* `callback_query`  — inline-keyboard button presses (Approve/Revise/Reject)
* `channel_post`    — posts in channels the bot is admin of (treated as DMs)
"""
from __future__ import annotations

import hmac
import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import TriggerAdapter, TriggerEvent

log = get_logger(__name__)


@register_plugin("trigger", "telegram", api_version="1.0", category="messaging")
class TelegramTrigger(TriggerAdapter):
    display_name = "Telegram (Bot API)"
    description = (
        "Send a message to your Telegram bot to kick off a workflow; drafts "
        "come back as a Telegram message with Approve / Revise / Reject buttons."
    )
    config_schema = {
        "type": "object",
        "properties": {
            "bot_token":     {"type": "string",
                              "title": "Bot token (from @BotFather)",
                              "x-secret": True},
            "bot_token_env": {"type": "string", "default": "TELEGRAM_BOT_TOKEN",
                              "title": "Fallback env var if bot_token isn't set inline"},
            "secret_token":  {"type": "string",
                              "title": "Secret token (set when calling setWebhook)"},
            "allowed_chat_ids": {"type": "array", "items": {"type": "string"},
                                 "title": "Whitelist of chat ids (empty = any)"},
        },
    }

    # ── signature verification ───────────────────────────────────────
    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        secret = self.config.get("secret_token", "")
        if not secret:
            return True   # optional; the URL itself contains a UUID
        sent = (
            headers.get("X-Telegram-Bot-Api-Secret-Token")
            or headers.get("x-telegram-bot-api-secret-token", "")
        )
        return hmac.compare_digest(secret, sent)

    def _resolve_token(self) -> str:
        """Get the bot token for THIS trigger. Mirrors the same
        resolution logic the review-channel side uses so a single
        bot config — inline ``bot_token`` per trigger or a fallback
        env var for single-bot self-hosted deployments — works for
        BOTH the inbound webhook (this adapter) AND the outbound
        review messages (the review channel adapter).
        """
        inline = (self.config.get("bot_token") or "").strip()
        if inline:
            return inline
        return os.getenv(
            self.config.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "",
        )

    # ── parsing ──────────────────────────────────────────────────────
    async def parse(self, payload: dict[str, Any]) -> list[TriggerEvent]:
        events: list[TriggerEvent] = []

        # Regular text messages (DM, group, channel_post)
        msg = payload.get("message") or payload.get("channel_post")
        if msg:
            ev = await self._parse_message(payload, msg)
            if ev is not None:
                events.append(ev)

        # Inline-keyboard button presses
        cq = payload.get("callback_query")
        if cq:
            events.append(self._parse_callback(payload, cq))

        return events

    # ── helpers ──────────────────────────────────────────────────────
    async def _parse_message(self, payload: dict[str, Any], msg: dict[str, Any]) -> TriggerEvent | None:
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if not self._allowed(chat_id):
            log.info("telegram_chat_not_allowed", chat_id=chat_id)
            return None
        text = msg.get("text") or msg.get("caption") or ""
        in_reply_to = (msg.get("reply_to_message") or {}).get("message_id")
        from_user = msg.get("from") or {}
        actor_id = str(from_user.get("id")) if from_user.get("id") is not None else None
        actor_handle = (
            from_user.get("username")
            and f"@{from_user['username']}"
        ) or None
        # Ingest any attached photos / videos / documents through
        # Telegram's getFile API → Supabase Storage so the URLs survive
        # outside the 1-hour file-CDN window AND can be attached to the
        # downstream Post's media. Without this, the executor sees
        # ``tg-file:<id>`` placeholder strings it can't fetch and the
        # user's image is silently dropped.
        file_ids = self._extract_file_ids(msg)
        org_id = str(self.config.get("__org_id__") or "shared")
        media_urls = await self._ingest_telegram_files(file_ids, org_id=org_id)
        return TriggerEvent(
            trigger_id=payload.get("trigger_id", ""),
            sender=chat_id,
            directive=text,
            media_urls=media_urls,
            raw=msg,
            in_reply_to=str(in_reply_to) if in_reply_to is not None else None,
            actor_id=actor_id,
            actor_handle=actor_handle,
        )

    @staticmethod
    def _parse_callback(payload: dict[str, Any], cq: dict[str, Any]) -> TriggerEvent:
        # Button data is one of "smms_approve" / "smms_revise" / "smms_reject"
        data = cq.get("data", "")
        message = cq.get("message") or {}
        original_msg_id = message.get("message_id")
        # In a group chat, the chat is the group; the tapper is `from`. Both
        # matter — chat_id is the address we reply to, actor_id is *who* tapped.
        chat_id = str((message.get("chat") or cq.get("from") or {}).get("id", ""))
        from_user = cq.get("from") or {}
        actor_id = str(from_user.get("id")) if from_user.get("id") is not None else None
        actor_handle = (
            from_user.get("username") and f"@{from_user['username']}"
        ) or (
            from_user.get("first_name") or None
        )
        return TriggerEvent(
            trigger_id=payload.get("trigger_id", ""),
            sender=chat_id,
            directive=_button_to_text(data),
            raw=cq,
            in_reply_to=str(original_msg_id) if original_msg_id is not None else None,
            actor_id=actor_id,
            actor_handle=actor_handle,
        )

    @staticmethod
    def _extract_file_ids(msg: dict[str, Any]) -> list[tuple[str, str]]:
        """Return a list of (kind, file_id) tuples for every media
        attachment on a Telegram message. ``kind`` is the original
        message field — ``photo``/``video``/``document``/``audio``/
        ``voice`` — used later to set MediaKind. For ``photo``, we
        pick the LAST entry in the size array (highest resolution).
        """
        out: list[tuple[str, str]] = []
        for k in ("photo", "video", "document", "audio", "voice"):
            v = msg.get(k)
            if isinstance(v, list) and v:
                file_id = v[-1].get("file_id")
                if file_id:
                    out.append((k, file_id))
            elif isinstance(v, dict):
                file_id = v.get("file_id")
                if file_id:
                    out.append((k, file_id))
        return out

    async def _ingest_telegram_files(
        self, file_specs: list[tuple[str, str]], *, org_id: str,
    ) -> list[str]:
        """Download every Telegram file via getFile + the file CDN and
        re-host into Supabase Storage. Returns the resulting public URLs.

        Two-step Telegram API:
          1. ``getFile?file_id=X`` → ``{ok: true, result: {file_path: "photos/Y.jpg"}}``
          2. ``GET https://api.telegram.org/file/bot<TOKEN>/<file_path>``
             returns the raw bytes.

        Per-file failures are logged but never raised — one bad file
        shouldn't sink an entire workflow run. Empty list when there's
        no bot_token configured (dry-run mode for tests) so the rest
        of the parse path still works.
        """
        if not file_specs:
            return []
        token = self._resolve_token()
        if not token:
            log.info("telegram_media_dry_run_no_token", count=len(file_specs))
            return []
        urls: list[str] = []
        # OUTER try/except wraps EVERYTHING — including the
        # MediaImportService() / SupabaseStorage() construction. If any
        # of that throws (env var missing, Supabase client init bug,
        # whatever) we degrade gracefully to "no media this round"
        # instead of 500-ing the entire webhook. The directive still
        # flows through and the agents will use AI-generated media or
        # text-only output.
        try:
            from app.services.media_import import MediaImportService
            importer = MediaImportService()
            async with httpx.AsyncClient(timeout=30.0) as client:
                for kind, file_id in file_specs:
                    try:
                        meta = await client.get(
                            f"https://api.telegram.org/bot{token}/getFile",
                            params={"file_id": file_id},
                        )
                        meta.raise_for_status()
                        file_path = (meta.json().get("result") or {}).get("file_path")
                        if not file_path:
                            log.warning("telegram_getfile_no_path",
                                        file_id=file_id[:24])
                            continue
                        # Download the raw bytes — Telegram's file CDN
                        # serves them at a separate, token-scoped URL.
                        dl = await client.get(
                            f"https://api.telegram.org/file/bot{token}/{file_path}",
                        )
                        dl.raise_for_status()
                        # Content-Type is usually right on Telegram's
                        # CDN; the importer also sniffs the filename.
                        ct = (dl.headers.get("content-type", "")
                              .split(";")[0].strip().lower())
                        result = await importer.import_bytes(
                            org_id=org_id,
                            data=dl.content,
                            content_type=ct or "application/octet-stream",
                            filename=file_path.rsplit("/", 1)[-1],
                            source_url=None,
                        )
                        urls.append(result.url)
                    except Exception as exc:                       # noqa: BLE001
                        log.warning(
                            "telegram_file_ingest_failed",
                            file_id=file_id[:24], kind=kind,
                            error=str(exc)[:200],
                        )
        except Exception as exc:                                   # noqa: BLE001
            # Constructor failures (SupabaseStorage init, importer
            # plugin missing, etc.) land here. Logged + we return an
            # empty list so the rest of the parse path still works.
            log.warning("telegram_media_pipeline_init_failed", error=str(exc))
        if urls:
            log.info("telegram_media_ingested",
                     count=len(urls), of=len(file_specs))
        return urls

    def _allowed(self, chat_id: str) -> bool:
        allow = self.config.get("allowed_chat_ids") or []
        return not allow or chat_id in {str(x) for x in allow}


_BUTTON_MAP = {
    "smms_approve": "Approve",
    "smms_revise":  "Revise",
    "smms_reject":  "Reject",
}


def _button_to_text(data: str) -> str:
    return _BUTTON_MAP.get(data, data)
