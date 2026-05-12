"""Azure OpenAI provider — same shape as OpenAI but with Azure resource URL."""
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


@register_plugin("llm", "azure_openai", api_version="1.0", category="hosted")
class AzureOpenAIProvider(LLMProvider):
    display_name = "Azure OpenAI"
    default_model = "gpt-4o"

    def __init__(
        self,
        endpoint: str | None = None,
        deployment: str | None = None,
        api_key: str | None = None,
        api_version: str = "2024-08-01-preview",
    ) -> None:
        self.endpoint = (endpoint or os.getenv("LLM_AZURE_ENDPOINT", "")).rstrip("/")
        self.deployment = deployment or os.getenv("LLM_AZURE_DEPLOYMENT", "")
        self.api_key = api_key or os.getenv("LLM_AZURE_API_KEY", "")
        self.api_version = api_version

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if not (self.endpoint and self.deployment and self.api_key):
            if _mock_fallback_allowed():
                from .anthropic import _mock_response
                return _mock_response(
                    req, self.deployment or self.default_model, "azure_openai",
                )
            raise MissingLLMCredentialError(
                "azure_openai",
                env_var="LLM_AZURE_ENDPOINT / LLM_AZURE_DEPLOYMENT / LLM_AZURE_API_KEY",
            )
        url = (
            f"{self.endpoint}/openai/deployments/{self.deployment}"
            f"/chat/completions?api-version={self.api_version}"
        )
        msgs: list[dict[str, str]] = []
        if req.system:
            msgs.append({"role": "system", "content": req.system})
        msgs.append({"role": "user", "content": req.prompt})
        body: dict[str, Any] = {
            "messages": msgs,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(url, json=body, headers={"api-key": self.api_key})
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, ValueError):
            if _mock_fallback_allowed():
                from .anthropic import _mock_response
                return _mock_response(req, self.deployment, "azure_openai")
            raise
        text = (data["choices"][0]["message"]["content"] or "").strip()
        u = data.get("usage", {})
        return LLMResponse(
            text=text, model=self.deployment,
            usage=LLMUsage(
                input_tokens=int(u.get("prompt_tokens", 0)),
                output_tokens=int(u.get("completion_tokens", 0)),
            ),
            raw=data,
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)
