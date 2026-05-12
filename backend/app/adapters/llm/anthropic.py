"""Anthropic Claude provider — uses the official SDK when an API key is set.

Behavior when no API key is configured:
  * In normal runs the call raises ``MissingLLMCredentialError`` so the
    workflow lands in FAILED with an actionable error message. This
    replaces the old silent fallback to a "[mock anthropic] ..." string
    which produced meaningless drafts.
  * In environments that set ``LLM_ALLOW_MOCK_FALLBACK=1`` (legacy
    demos / smoke tests) the old mock path is preserved so existing
    fixtures keep working.

The dedicated ``mock`` provider (``llm/mock.py``) is still available for
deterministic tests — it's selected explicitly via workflow config, not
by accident through a missing key.
"""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import (
    LLMProvider, LLMRequest, LLMResponse, LLMUsage,
    MissingLLMCredentialError,
)

log = get_logger(__name__)


def _mock_fallback_allowed() -> bool:
    """Opt-in escape hatch for legacy smoke tests and demos. Off in prod."""
    return os.getenv("LLM_ALLOW_MOCK_FALLBACK", "0").lower() in (
        "1", "true", "yes", "on",
    )


@register_plugin("llm", "anthropic", api_version="1.0")
class AnthropicProvider(LLMProvider):
    display_name = "Anthropic Claude"
    default_model = "claude-sonnet-4-6"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("LLM_ANTHROPIC_API_KEY", "")
        self._client: Any | None = None
        if self.api_key:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
            except ImportError:
                log.warning("anthropic_sdk_missing")

    async def complete(self, req: LLMRequest) -> LLMResponse:
        model = req.model or self.default_model
        if self._client is None:
            if _mock_fallback_allowed():
                return _mock_response(req, model, "anthropic")
            raise MissingLLMCredentialError(
                "anthropic", env_var="LLM_ANTHROPIC_API_KEY",
            )
        # Claude's multimodal input is an array of content blocks under
        # the user message. We append image blocks BEFORE the text so
        # the model sees the image first (Anthropic's recommended order
        # for vision-heavy prompts — text-after gives the model context
        # about what to look for).
        content_blocks: list[dict[str, Any]] = []
        for img_url in req.image_urls or ():
            if not img_url:
                continue
            content_blocks.append({
                "type": "image",
                "source": {"type": "url", "url": img_url},
            })
        content_blocks.append({"type": "text", "text": req.prompt})

        msg = await self._client.messages.create(
            model=model,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            system=req.system or "",
            messages=[{"role": "user", "content": content_blocks}],
            stop_sequences=req.stop or [],
        )
        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        usage = LLMUsage(
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )
        return LLMResponse(text=text, model=model, usage=usage, raw=msg.model_dump())

    def count_tokens(self, text: str) -> int:
        # Rough heuristic; replace with anthropic.tokenizer when available.
        return max(1, len(text) // 4)


def _mock_response(req: LLMRequest, model: str, provider: str) -> LLMResponse:
    text = f"[mock {provider}] {req.prompt[:120]}…"
    return LLMResponse(
        text=text, model=model,
        usage=LLMUsage(input_tokens=len(req.prompt) // 4, output_tokens=len(text) // 4),
        raw={"mocked": True},
    )
