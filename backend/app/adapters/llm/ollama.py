"""Ollama provider — runs any local LLM (Llama, Mistral, Qwen, Phi, etc.)
through a self-hosted Ollama daemon.

Endpoint: POST {base_url}/api/chat with {model, messages[], stream:false}
Default base_url: http://localhost:11434
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .anthropic import _mock_fallback_allowed
from .base import LLMProvider, LLMRequest, LLMResponse, LLMUsage

log = get_logger(__name__)


@register_plugin("llm", "ollama", api_version="1.0", category="self-hosted")
class OllamaProvider(LLMProvider):
    display_name = "Ollama (self-hosted)"
    default_model = "llama3"

    config_schema = {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "default": "http://localhost:11434",
                         "title": "Ollama base URL"},
            "model":    {"type": "string", "default": "llama3",
                         "title": "Default model (e.g. llama3, qwen2.5, mistral, phi3)"},
        },
    }

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("LLM_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("LLM_OLLAMA_MODEL", self.default_model)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        model = req.model or self.model
        body: dict[str, Any] = {
            "model": model,
            "messages": _build_messages(req),
            "stream": False,
            "options": {
                "temperature": req.temperature,
                "num_predict": req.max_tokens,
            },
        }
        if req.response_format == "json":
            body["format"] = "json"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(f"{self.base_url}/api/chat", json=body)
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, ValueError):
            if _mock_fallback_allowed():
                from .anthropic import _mock_response
                return _mock_response(req, model, "ollama")
            raise

        text = (data.get("message", {}).get("content") or "").strip()
        usage = LLMUsage(
            input_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
        )
        return LLMResponse(text=text, model=model, usage=usage, raw=data)

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


def _build_messages(req: LLMRequest) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    if req.system:
        msgs.append({"role": "system", "content": req.system})
    msgs.append({"role": "user", "content": req.prompt})
    return msgs
