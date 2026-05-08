"""Budget-guarded LLM wrapper.

Any concrete :class:`LLMProvider` can be wrapped with :class:`BudgetGuardedLLM`
to:

* check the org's MTD spend before every ``complete()`` call (raise
  :class:`LLMBudgetExceededError` if over),
* compute a ``cost_usd`` from the model's pricing if the underlying provider
  didn't return one,
* persist a row to ``smms.llm_usage`` after a successful completion.

The wrapper is transparent — it implements the same :class:`LLMProvider`
interface so callers (the orchestrator, RAG retriever, etc.) don't need to
know about it.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.domain.value_objects.ids import OrgId

from .base import LLMProvider, LLMRequest, LLMResponse, LLMUsage
from .pricing import estimate_cost_usd

log = get_logger(__name__)


class BudgetGuardedLLM(LLMProvider):
    """Wraps another :class:`LLMProvider` with budget + usage tracking.

    Sub-classing :class:`LLMProvider` keeps the public surface identical so
    we can drop this wrapper anywhere a provider is expected.
    """

    plugin_name = "budget_guarded"
    api_version = "1.0"

    def __init__(
        self,
        inner: LLMProvider,
        *,
        org_id: OrgId,
        provider_name: str,
        usage_service: Any,                 # LlmUsageService — typed loosely to avoid cycles
        context: dict[str, Any] | None = None,
    ) -> None:
        self._inner = inner
        self._org_id = org_id
        self._provider_name = provider_name
        self._usage = usage_service
        self._context = context or {}
        # Mirror inner's display fields so introspection still works.
        self.display_name = getattr(inner, "display_name", "")
        self.default_model = getattr(inner, "default_model", "")

    # ── delegation ──────────────────────────────────────────────────────
    def count_tokens(self, text: str) -> int:
        return self._inner.count_tokens(text)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._inner.embed(texts)

    # ── budget-aware complete() ─────────────────────────────────────────
    async def complete(self, req: LLMRequest) -> LLMResponse:
        # 1. Enforce budget before spending money.
        await self._usage.check_budget(self._org_id)

        # 2. Run the actual completion.
        resp = await self._inner.complete(req)

        # 3. Compute cost if the provider didn't, then record.
        usage = resp.usage
        cost = usage.cost_usd
        if not cost and (usage.input_tokens or usage.output_tokens):
            cost = estimate_cost_usd(resp.model, usage.input_tokens, usage.output_tokens)
            usage = LLMUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=cost,
            )
            resp = replace(resp, usage=usage)

        # Fire-and-forget the insert — never block the caller on the ledger
        # write, but log if it fails so we can investigate.
        asyncio.create_task(self._safe_record(resp))
        return resp

    async def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        # We can't easily compute cost from a stream without re-tokenising,
        # so we fall back to the default "buffer-then-yield" behaviour the
        # base class provides. That goes through complete() which is guarded.
        async for chunk in super().stream(req):
            yield chunk

    async def _safe_record(self, resp: LLMResponse) -> None:
        try:
            await self._usage.record(
                self._org_id,
                provider=self._provider_name,
                model=resp.model,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cost_usd=resp.usage.cost_usd,
                context=self._context,
            )
        except Exception as exc:                                        # noqa: BLE001
            log.warning(
                "llm_usage_record_failed",
                org_id=str(self._org_id),
                provider=self._provider_name,
                error=str(exc),
            )
