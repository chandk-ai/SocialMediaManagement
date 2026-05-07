"""Generic OpenAI-compatible provider — works with any service exposing the
OpenAI Chat Completions API: vLLM, LM Studio, LocalAI, Together AI, Groq,
Fireworks, Mistral La Plateforme, DashScope (Qwen), DeepSeek, Perplexity, ...

This single adapter covers the long tail of open / commercial models because
they all standardised on the OpenAI request shape.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import LLMProvider, LLMRequest, LLMResponse, LLMUsage

log = get_logger(__name__)


@register_plugin("llm", "openai_compatible", api_version="1.0", category="byo-endpoint")
class OpenAICompatibleProvider(LLMProvider):
    display_name = "OpenAI-compatible endpoint (vLLM / Together / Groq / Qwen / …)"
    default_model = ""

    config_schema = {
        "type": "object",
        "required": ["base_url", "model"],
        "properties": {
            "base_url": {"type": "string", "title": "Base URL",
                         "examples": [
                             "http://localhost:8001/v1",
                             "https://api.together.xyz/v1",
                             "https://api.groq.com/openai/v1",
                             "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                         ]},
            "model":    {"type": "string", "title": "Model id",
                         "examples": [
                             "qwen2.5-72b-instruct",
                             "Mixtral-8x7B-Instruct-v0.1",
                             "llama-3.1-70b-versatile",
                         ]},
            "api_key_env": {"type": "string",
                            "title": "Env var holding the API key",
                            "default": "LLM_OPENAI_COMPATIBLE_API_KEY"},
        },
    }

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "LLM_OPENAI_COMPATIBLE_API_KEY",
    ) -> None:
        self.base_url = (base_url or os.getenv("LLM_OPENAI_COMPATIBLE_BASE_URL", "")).rstrip("/")
        self.model = model or os.getenv("LLM_OPENAI_COMPATIBLE_MODEL", "")
        self.api_key = api_key or os.getenv(api_key_env, "")

    async def complete(self, req: LLMRequest) -> LLMResponse:
        model = req.model or self.model
        if not self.base_url or not model:
            from .anthropic import _mock_response
            return _mock_response(req, model or "openai-compatible", "openai_compatible")

        msgs: list[dict[str, str]] = []
        if req.system:
            msgs.append({"role": "system", "content": req.system})
        msgs.append({"role": "user", "content": req.prompt})

        body: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.stop:
            body["stop"] = req.stop
        if req.response_format == "json":
            body["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions", json=body, headers=headers
                )
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, ValueError):
            from .anthropic import _mock_response
            return _mock_response(req, model, "openai_compatible")

        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message", {}).get("content") or "").strip()
        usage_data = data.get("usage", {})
        return LLMResponse(
            text=text, model=model,
            usage=LLMUsage(
                input_tokens=int(usage_data.get("prompt_tokens", 0)),
                output_tokens=int(usage_data.get("completion_tokens", 0)),
            ),
            raw=data,
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)
