"""Anthropic Claude provider — uses the official SDK when an API key is set,
falls back to a deterministic mock otherwise so the system runs out of the box.
"""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import LLMProvider, LLMRequest, LLMResponse, LLMUsage

log = get_logger(__name__)


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
            return _mock_response(req, model, "anthropic")
        msg = await self._client.messages.create(
            model=model,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            system=req.system or "",
            messages=[{"role": "user", "content": req.prompt}],
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
