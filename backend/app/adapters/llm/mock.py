"""A fully deterministic mock provider for tests and the demo experience."""
from __future__ import annotations

import json

from app.plugins.registry import register_plugin

from .base import LLMProvider, LLMRequest, LLMResponse, LLMUsage


@register_plugin("llm", "mock", api_version="1.0")
class MockProvider(LLMProvider):
    display_name = "Mock LLM"
    default_model = "mock-1"

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if req.response_format == "json":
            text = json.dumps({
                "echo": req.prompt[:80],
                "system": req.system,
                "tokens_in": self.count_tokens(req.prompt),
            })
        else:
            text = f"[mock] {req.prompt.strip()[:280]}"
        return LLMResponse(
            text=text,
            model=req.model or self.default_model,
            usage=LLMUsage(
                input_tokens=self.count_tokens(req.prompt),
                output_tokens=self.count_tokens(text),
            ),
            raw={"mocked": True},
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)
