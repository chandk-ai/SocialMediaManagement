"""AWS Bedrock provider — uses boto3 if available, else mock."""
from __future__ import annotations

import json
import os
from typing import Any

from app.plugins.registry import register_plugin

from .base import LLMProvider, LLMRequest, LLMResponse, LLMUsage


@register_plugin("llm", "bedrock", api_version="1.0", category="hosted")
class BedrockProvider(LLMProvider):
    display_name = "AWS Bedrock"
    default_model = "anthropic.claude-3-5-sonnet-20240620-v1:0"

    def __init__(self, region: str | None = None, model: str | None = None) -> None:
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.model = model or os.getenv("LLM_BEDROCK_MODEL", self.default_model)
        self._client: Any | None = None
        try:
            import boto3
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        except ImportError:
            pass

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if self._client is None:
            from .anthropic import _mock_response
            return _mock_response(req, self.model, "bedrock")

        # Anthropic-shaped body works for Claude models on Bedrock; for other
        # model families adjust accordingly.
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": [{"role": "user", "content": req.prompt}],
        }
        if req.system:
            body["system"] = req.system
        resp = self._client.invoke_model(
            modelId=self.model, body=json.dumps(body),
            contentType="application/json", accept="application/json",
        )
        data = json.loads(resp["body"].read())
        text = "".join(b.get("text", "") for b in data.get("content", []))
        usage = data.get("usage", {})
        return LLMResponse(
            text=text, model=self.model,
            usage=LLMUsage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
            ),
            raw=data,
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)
