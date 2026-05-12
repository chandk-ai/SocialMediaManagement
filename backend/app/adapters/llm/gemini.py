"""Google Gemini provider — generative-language v1beta REST endpoint."""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.plugins.registry import register_plugin

from .anthropic import _mock_fallback_allowed
from .base import (
    LLMProvider, LLMRequest, LLMResponse, LLMUsage,
    MissingLLMCredentialError,
)


@register_plugin("llm", "gemini", api_version="1.0", category="hosted")
class GeminiProvider(LLMProvider):
    display_name = "Google Gemini"
    default_model = "gemini-1.5-pro"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("LLM_GEMINI_API_KEY", "")
        self.model = model or os.getenv("LLM_GEMINI_MODEL", self.default_model)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if not self.api_key:
            if _mock_fallback_allowed():
                from .anthropic import _mock_response
                return _mock_response(req, self.model, "gemini")
            raise MissingLLMCredentialError(
                "gemini", env_var="LLM_GEMINI_API_KEY",
            )
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": req.prompt}]}],
            "generationConfig": {
                "temperature": req.temperature,
                "maxOutputTokens": req.max_tokens,
            },
        }
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}
        if req.response_format == "json":
            body["generationConfig"]["responseMimeType"] = "application/json"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(url, json=body)
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, ValueError):
            if _mock_fallback_allowed():
                from .anthropic import _mock_response
                return _mock_response(req, self.model, "gemini")
            raise  # let the worker retry / DLQ — don't fake content.

        text = ""
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            pass
        usage = data.get("usageMetadata", {})
        return LLMResponse(
            text=text.strip(), model=self.model,
            usage=LLMUsage(
                input_tokens=int(usage.get("promptTokenCount", 0)),
                output_tokens=int(usage.get("candidatesTokenCount", 0)),
            ),
            raw=data,
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)
