"""LLM provider contract — all GenAI work goes through this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, ClassVar


class LLMProviderError(Exception):
    """Base class for provider-level failures the orchestrator should
    surface to the user (vs. transient network glitches the worker
    retries automatically)."""


class MissingLLMCredentialError(LLMProviderError):
    """Raised when a hosted LLM provider is invoked without an API key
    (and the install isn't running in explicit-mock mode). This is a
    *permanent* config error — the worker should fail the run with a
    clear message instead of producing junk drafts via the mock path."""

    def __init__(self, provider: str, env_var: str | None = None) -> None:
        self.provider = provider
        self.env_var = env_var
        msg = f"No API key configured for LLM provider '{provider}'."
        if env_var:
            msg += (f" Set {env_var} (or store the key per-org via "
                    f"Settings → LLM credentials).")
        else:
            msg += " Store the key per-org via Settings → LLM credentials."
        super().__init__(msg)


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class LLMRequest:
    prompt: str
    system: str | None = None
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1024
    stop: list[str] | None = None
    response_format: str | None = None     # e.g. "json"
    metadata: dict[str, Any] = field(default_factory=dict)
    # Public HTTPS URLs of image attachments. When set, vision-capable
    # providers (Anthropic Claude, Gemini, OpenAI gpt-4o) embed these
    # as multimodal content alongside the prompt so the model can
    # actually *see* what the source contains — captions can then
    # describe the image rather than hallucinating from text alone.
    # Providers that don't support vision quietly ignore this field.
    image_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    model: str
    usage: LLMUsage
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    plugin_name: ClassVar[str] = ""
    api_version: ClassVar[str] = "1.0"
    display_name: ClassVar[str] = ""
    default_model: ClassVar[str] = ""

    @abstractmethod
    async def complete(self, req: LLMRequest) -> LLMResponse: ...

    async def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        """Default fallback: emit the full completion as a single chunk."""
        resp = await self.complete(req)
        yield resp.text

    @abstractmethod
    def count_tokens(self, text: str) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(f"{self.plugin_name} does not support embeddings")
