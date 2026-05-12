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
#
# NOTE on availability (May 2026): Google retires older model ids for
# *new* users while keeping them for existing customers. So a default
# that works for one tenant may 404 for another. The provider therefore
# performs **dynamic discovery** when its configured model 404s — it
# lists the models the user's key actually has access to and picks the
# best matching one. See ``_discover_fallback_model``.
_MODEL_ALIASES: dict[str, str] = {
    "gemini-1.5-pro": "gemini-1.5-pro-latest",
    "gemini-1.5-flash": "gemini-1.5-flash-latest",
    "gemini-2.0-pro": "gemini-2.0-pro-exp",
    "gemini-2.0-flash": "gemini-2.0-flash",
    "gemini-pro": "gemini-flash-latest",
}

# Preference order when auto-picking a fallback after a 404. We prefer
# Flash (fastest + cheapest + most likely to be available on free tier)
# over Pro, and newer over older.
_AUTO_PICK_PREFERENCE = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-latest",
    "gemini-flash-latest",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-pro-latest",
    "gemini-pro-latest",
    "gemini-1.5-pro-latest",
]


class _ModelNotFound(Exception):
    """Internal signal that the configured Gemini model returned 404 so
    ``complete()`` can attempt auto-fallback. Not exported."""

    def __init__(self, *, model: str, message: str, body: str) -> None:
        self.model = model
        self.message = message
        self.body = body
        super().__init__(f"model not found: {model}")


def _scrub_key(text: str, api_key: str) -> str:
    """Mask the API key anywhere it might appear in a log line / error."""
    if not api_key or not text:
        return text
    masked = api_key[:6] + "…" + api_key[-4:] if len(api_key) > 12 else "***"
    return text.replace(api_key, masked)


@register_plugin("llm", "gemini", api_version="1.0", category="hosted")
class GeminiProvider(LLMProvider):
    display_name = "Google Gemini"
    # Use the `-latest` alias by default — it auto-tracks the current
    # generation Google makes available to new users without us needing
    # to chase model-id changes every quarter.
    default_model = "gemini-flash-latest"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("LLM_GEMINI_API_KEY", "")
        raw_model = model or os.getenv("LLM_GEMINI_MODEL", self.default_model)
        self.model = _MODEL_ALIASES.get(raw_model, raw_model)
        # Cache of models the key has been observed to have access to —
        # populated lazily on the first 404. Avoids re-discovering on
        # every call within the same process.
        self._available_models: list[str] | None = None

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if not self.api_key:
            if _mock_fallback_allowed():
                from .anthropic import _mock_response
                return _mock_response(req, self.model, "gemini")
            raise MissingLLMCredentialError(
                "gemini", env_var="LLM_GEMINI_API_KEY",
            )

        # Try the configured model first. If we get a 404 ("model not
        # available to your key"), discover what IS available and retry
        # against the best match. We only retry once — if the fallback
        # also fails, we surface a clear error listing what the key has
        # access to so the user can update their config.
        try:
            return await self._invoke(self.model, req)
        except _ModelNotFound as primary_404:
            log.info("gemini_primary_model_not_found",
                     model=self.model, body=primary_404.body[:200])
            fallback = await self._discover_fallback_model()
            if not fallback or fallback == self.model:
                # Couldn't find an alternative — re-raise with the
                # discovered list so the user knows what to set.
                available = ", ".join(self._available_models or []) or "(none)"
                raise RuntimeError(
                    f"Gemini API 404 for model '{self.model}'. {primary_404.message} "
                    f"Models available to your API key: {available}. Update "
                    f"LLM_GEMINI_MODEL or the workflow config and re-run."
                ) from None

            log.info("gemini_fallback_model_selected",
                     primary=self.model, fallback=fallback)
            # Stick with the discovered model for this provider
            # instance's lifetime — saves the discovery round-trip on
            # every subsequent call within the same process.
            self.model = fallback
            try:
                return await self._invoke(fallback, req)
            except _ModelNotFound as second_404:
                available = ", ".join(self._available_models or []) or "(none)"
                raise RuntimeError(
                    f"Gemini API 404 even after auto-fallback to '{fallback}'. "
                    f"{second_404.message} Models available: {available}."
                ) from None

    async def _invoke(self, model: str, req: LLMRequest) -> LLMResponse:
        """Single Gemini API call. Raises ``_ModelNotFound`` on 404 so
        ``complete()`` can attempt auto-fallback. Other HTTP errors are
        translated to clean RuntimeError messages (key-scrubbed)."""
        # NEVER put the key in the URL — it ends up in error messages.
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
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
            status = exc.response.status_code
            try:
                text_body = exc.response.text[:500]
            except Exception:                                         # noqa: BLE001
                text_body = ""
            scrubbed_body = _scrub_key(text_body, self.api_key)
            if status == 404:
                # Try to lift Google's "message" field for a cleaner display.
                try:
                    msg = exc.response.json().get("error", {}).get("message", "")
                except Exception:                                     # noqa: BLE001
                    msg = ""
                raise _ModelNotFound(model=model, message=msg or scrubbed_body,
                                     body=scrubbed_body) from None
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
                return _mock_response(req, model, "gemini")
            scrubbed = _scrub_key(str(exc), self.api_key)
            raise RuntimeError(f"Gemini API error: {scrubbed}") from None

        text = ""
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            pass
        usage = data.get("usageMetadata", {})
        return LLMResponse(
            text=text.strip(), model=model,
            usage=LLMUsage(
                input_tokens=int(usage.get("promptTokenCount", 0)),
                output_tokens=int(usage.get("candidatesTokenCount", 0)),
            ),
            raw=data,
        )

    async def _discover_fallback_model(self) -> str | None:
        """Query Google's /v1beta/models endpoint to learn which models
        this API key actually has access to, then pick the best one
        per ``_AUTO_PICK_PREFERENCE``. Returns None if the listing fails
        or nothing matches."""
        if self._available_models is None:
            try:
                self._available_models = await self._list_models()
            except Exception as exc:                                  # noqa: BLE001
                scrubbed = _scrub_key(str(exc), self.api_key)
                log.warning("gemini_list_models_failed", error=scrubbed)
                return None

        avail = set(self._available_models or [])
        if not avail:
            return None

        # First pass: prefer models matching the user's intent (flash
        # vs pro) — if their original choice contained "pro", prefer
        # Pro models in the fallback list; otherwise Flash.
        prefers_pro = "pro" in self.model.lower()
        for candidate in _AUTO_PICK_PREFERENCE:
            cand_is_pro = "pro" in candidate
            if prefers_pro != cand_is_pro:
                continue
            if candidate in avail:
                return candidate

        # Second pass: relax the flash/pro preference.
        for candidate in _AUTO_PICK_PREFERENCE:
            if candidate in avail:
                return candidate

        # Last resort — pick the first available "generateContent"
        # model that contains "gemini" in its name.
        for m in sorted(avail):
            if "gemini" in m.lower() and "embedding" not in m.lower():
                return m
        return None

    async def _list_models(self) -> list[str]:
        """List model ids the API key has access to. Returns names
        without the ``models/`` prefix that Google returns."""
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        headers = {"x-goog-api-key": self.api_key}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
        names: list[str] = []
        for m in data.get("models") or []:
            name = (m.get("name") or "").removeprefix("models/")
            methods = m.get("supportedGenerationMethods") or []
            if name and "generateContent" in methods:
                names.append(name)
        return names

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
