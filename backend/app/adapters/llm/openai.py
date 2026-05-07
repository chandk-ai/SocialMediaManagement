"""OpenAI provider."""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import LLMProvider, LLMRequest, LLMResponse, LLMUsage

log = get_logger(__name__)


@register_plugin("llm", "openai", api_version="1.0")
class OpenAIProvider(LLMProvider):
    display_name = "OpenAI"
    default_model = "gpt-4o"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("LLM_OPENAI_API_KEY", "")
        self._client: Any | None = None
        if self.api_key:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self.api_key)
            except ImportError:
                log.warning("openai_sdk_missing")

    async def complete(self, req: LLMRequest) -> LLMResponse:
        model = req.model or self.default_model
        if self._client is None:
            from .anthropic import _mock_response
            return _mock_response(req, model, "openai")
        msgs = []
        if req.system:
            msgs.append({"role": "system", "content": req.system})
        msgs.append({"role": "user", "content": req.prompt})
        rsp = await self._client.chat.completions.create(
            model=model, messages=msgs,
            max_tokens=req.max_tokens, temperature=req.temperature,
            stop=req.stop,
            response_format={"type": "json_object"} if req.response_format == "json" else None,
        )
        choice = rsp.choices[0]
        return LLMResponse(
            text=choice.message.content or "",
            model=model,
            usage=LLMUsage(
                input_tokens=rsp.usage.prompt_tokens,
                output_tokens=rsp.usage.completion_tokens,
            ),
            raw=rsp.model_dump(),
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)
