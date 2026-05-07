"""LLM provider contract — all GenAI work goes through this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, ClassVar


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
