"""Hugging Face Inference API provider — covers the entire HF model catalog.

Endpoint: POST https://api-inference.huggingface.co/models/{model}
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.plugins.registry import register_plugin

from .base import LLMProvider, LLMRequest, LLMResponse, LLMUsage


@register_plugin("llm", "huggingface", api_version="1.0", category="hosted-os")
class HuggingFaceProvider(LLMProvider):
    display_name = "Hugging Face Inference"
    default_model = "meta-llama/Meta-Llama-3-8B-Instruct"

    config_schema = {
        "type": "object",
        "properties": {
            "model":   {"type": "string", "title": "Model id (HF hub)"},
            "api_token_env": {"type": "string", "default": "HF_TOKEN"},
            "endpoint": {"type": "string", "title": "Override endpoint (for dedicated endpoints)"},
        },
    }

    def __init__(self, model: str | None = None, api_token: str | None = None,
                 endpoint: str | None = None) -> None:
        self.model = model or os.getenv("LLM_HF_MODEL", self.default_model)
        self.token = api_token or os.getenv("HF_TOKEN", "")
        self.endpoint = endpoint or os.getenv("LLM_HF_ENDPOINT", "")

    async def complete(self, req: LLMRequest) -> LLMResponse:
        url = self.endpoint or f"https://api-inference.huggingface.co/models/{self.model}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body: dict[str, Any] = {
            "inputs": _build_prompt(req),
            "parameters": {
                "temperature": req.temperature,
                "max_new_tokens": req.max_tokens,
                "return_full_text": False,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(url, json=body, headers=headers)
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, ValueError):
            from .anthropic import _mock_response
            return _mock_response(req, self.model, "huggingface")

        text = ""
        if isinstance(data, list) and data:
            text = data[0].get("generated_text", "")
        elif isinstance(data, dict):
            text = data.get("generated_text", "")
        return LLMResponse(
            text=text.strip(), model=self.model,
            usage=LLMUsage(
                input_tokens=self.count_tokens(req.prompt),
                output_tokens=self.count_tokens(text),
            ),
            raw=data if isinstance(data, dict) else {"items": data},
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


def _build_prompt(req: LLMRequest) -> str:
    return f"{req.system}\n\n{req.prompt}" if req.system else req.prompt
