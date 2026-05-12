"""Google Gemini provider — generative-language v1beta REST endpoint.

Security notes:
  * The API key is sent as the ``x-goog-api-key`` header, NOT as a URL
    query parameter. The previous URL-param form leaked the key into
    httpx's exception messages (which then ended up in worker logs,
    job error rows, and Sentry breadcrumbs). Header form keeps the key
    out of every URL-based log line.
  * If the API ever does surface the key inside an error string, the
    ``_scrub_key`` helper masks it before we re-raise.

Default model:
  * Gemini 2.0 Flash is the current free-tier-friendly default. The
    earlier ``gemini-1.5-pro`` default 404'd against many keys because
    Google moved 1.5-Pro behind paid billing and prefers the
    ``-latest`` / numbered-version aliases when you do have access.
  * Operators can override via ``LLM_GEMINI_MODEL`` or per-workflow
    config.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .anthropic import _mock_fallback_allowed
from .base import (
    LLMProvider, LLMRequest, LLMResponse, LLMUsage,
    MissingLLMCredentialError,
)

log = get_logger(__name__)


# Friendly aliases users might type → real model id known to v1beta.
# Anything not in this map is passed through as-is.
_MODEL_ALIASES: dict[str, str] = {
    "gemini-1.5-pro": "gemini-1.5-pro-latest",
    "gemini-1.5-flash": "gemini-1.5-flash-latest",
    "gemini-2.0-pro": "gemini-2.0-pro-exp",
    "gemini-2.0-flash": "gemini-2.0-flash",
    "gemini-pro": "gemini-1.5-flash-latest",   # 1.0-pro is fully retired
}


def _scrub_key(text: str, api_key: str) -> str:
    """Mask the API key anywhere it might appear in a log line / error."""
    if not api_key or not text:
        return text
    masked = api_key[:6] + "…" + api_key[-4:] if len(api_key) > 12 else "***"
    return text.replace(api_key, masked)


@register_plugin("llm", "gemini", api_version="1.0", category="hosted")
class GeminiProvider(LLMProvider):
    display_name = "Google Gemini"
    default_model = "gemini-2.0-flash"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("LLM_GEMINI_API_KEY", "")
        raw_model = model or os.getenv("LLM_GEMINI_MODEL", self.default_model)
        self.model = _MODEL_ALIASES.get(raw_model, raw_model)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if not self.api_key:
            if _mock_fallback_allowed():
                from .anthropic import _mock_response
                return _mock_response(req, self.model, "gemini")
            raise MissingLLMCredentialError(
                "gemini", env_var="LLM_GEMINI_API_KEY",
            )

        # NEVER put the key in the URL — it ends up in error messages.
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
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
                r = await client.post(url, json=body, headers=headers)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as exc:
            # Surface 404 as a clear "wrong model name" error instead of
            # the raw httpx string (which can be confusing and which
            # historically leaked the key from the URL form).
            status = exc.response.status_code
            text_body = ""
            try:
                text_body = exc.response.text[:500]
            except Exception:                                         # noqa: BLE001
                text_body = ""
            scrubbed_body = _scrub_key(text_body, self.api_key)
            if status == 404:
                raise RuntimeError(
                    f"Gemini API 404 for model '{self.model}'. The model "
                    f"id may be retired or require paid billing on your key. "
                    f"Try setting LLM_GEMINI_MODEL to 'gemini-2.0-flash' or "
                    f"'gemini-1.5-flash-latest'. Body: {scrubbed_body}"
                ) from None
            if status in (401, 403):
                raise RuntimeError(
                    f"Gemini API {status} — the API key was rejected. "
                    f"Verify it at https://aistudio.google.com/app/apikey "
                    f"and that the Generative Language API is enabled. "
                    f"Body: {scrubbed_body}"
                ) from None
            raise RuntimeError(
                f"Gemini API {status}: {scrubbed_body}"
            ) from None
        except (httpx.HTTPError, ValueError) as exc:
            if _mock_fallback_allowed():
                from .anthropic import _mock_response
                return _mock_response(req, self.model, "gemini")
            # Don't leak the key into the exception chain.
            scrubbed = _scrub_key(str(exc), self.api_key)
            raise RuntimeError(f"Gemini API error: {scrubbed}") from None

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
