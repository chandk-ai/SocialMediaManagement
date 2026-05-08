"""Per-org LLM usage tracking + budget enforcement.

The append-only ``smms.llm_usage`` table is the single source of truth for
month-to-date spend. Two access patterns:

1. **Pre-call** — ``check_budget(org_id)`` sums the current month and raises
   :class:`LLMBudgetExceededError` if MTD ≥ ``monthly_llm_budget_usd``.
2. **Post-call** — ``record(org_id, ...)`` inserts one row per completion.

Budget data is *not* cached: we want a hard stop the moment the threshold is
crossed, even across worker pods. Postgres handles the load fine because the
query is a single indexed sum().
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.logging import get_logger
from app.domain.exceptions import LLMBudgetExceededError
from app.domain.value_objects.ids import OrgId

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UsageSummary:
    mtd_spend_usd: float
    budget_usd: float
    remaining_usd: float
    over_budget: bool
    billing_month: str          # ISO YYYY-MM-01


@dataclass(frozen=True, slots=True)
class UsageRow:
    occurred_at: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    context: dict[str, Any]


class LlmUsageService:
    """Records per-completion usage and enforces the org's monthly budget."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sm = session_factory

    @staticmethod
    def _billing_month(today: date | None = None) -> date:
        d = today or date.today()
        return date(d.year, d.month, 1)

    # ── reads ────────────────────────────────────────────────────────────
    async def mtd_spend(self, org_id: OrgId) -> float:
        async with self._sm() as s:
            row = (await s.execute(
                text(
                    "SELECT coalesce(sum(cost_usd), 0) AS total "
                    "FROM smms.llm_usage "
                    "WHERE org_id = :org_id AND billing_month = :month"
                ),
                {"org_id": UUID(str(org_id)), "month": self._billing_month()},
            )).first()
        return float(row.total) if row and row.total is not None else 0.0

    async def org_budget(self, org_id: OrgId) -> float:
        async with self._sm() as s:
            row = (await s.execute(
                text(
                    "SELECT monthly_llm_budget_usd AS b "
                    "FROM smms.organizations WHERE id = :org_id"
                ),
                {"org_id": UUID(str(org_id))},
            )).first()
        return float(row.b) if row and row.b is not None else 0.0

    async def summary(self, org_id: OrgId) -> UsageSummary:
        spent = await self.mtd_spend(org_id)
        budget = await self.org_budget(org_id)
        return UsageSummary(
            mtd_spend_usd=round(spent, 4),
            budget_usd=round(budget, 4),
            remaining_usd=round(max(0.0, budget - spent), 4),
            over_budget=budget > 0 and spent >= budget,
            billing_month=self._billing_month().isoformat(),
        )

    async def recent(self, org_id: OrgId, *, limit: int = 50) -> list[UsageRow]:
        async with self._sm() as s:
            result = await s.execute(
                text(
                    "SELECT occurred_at, provider, model, input_tokens, "
                    "       output_tokens, cost_usd, context "
                    "FROM smms.llm_usage "
                    "WHERE org_id = :org_id "
                    "ORDER BY occurred_at DESC LIMIT :limit"
                ),
                {"org_id": UUID(str(org_id)), "limit": int(limit)},
            )
            rows = result.fetchall()
        return [
            UsageRow(
                occurred_at=r.occurred_at.isoformat() if r.occurred_at else "",
                provider=r.provider,
                model=r.model,
                input_tokens=r.input_tokens or 0,
                output_tokens=r.output_tokens or 0,
                cost_usd=float(r.cost_usd or 0),
                context=r.context if isinstance(r.context, dict) else (json.loads(r.context) if r.context else {}),
            )
            for r in rows
        ]

    # ── writes ───────────────────────────────────────────────────────────
    async def record(
        self,
        org_id: OrgId,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        async with self._sm() as s:
            await s.execute(
                text(
                    "INSERT INTO smms.llm_usage "
                    "(org_id, provider, model, input_tokens, output_tokens, "
                    " cost_usd, billing_month, context) "
                    "VALUES (:org_id, :provider, :model, :inp, :out, :cost, "
                    "        :month, cast(:ctx as jsonb))"
                ),
                {
                    "org_id": UUID(str(org_id)),
                    "provider": provider,
                    "model": model,
                    "inp": int(input_tokens),
                    "out": int(output_tokens),
                    "cost": round(float(cost_usd), 6),
                    "month": self._billing_month(),
                    "ctx": json.dumps(context or {}),
                },
            )
            await s.commit()

    # ── enforcement ──────────────────────────────────────────────────────
    async def check_budget(self, org_id: OrgId) -> None:
        """Raise :class:`LLMBudgetExceededError` if the org has hit its cap.

        A budget of 0 is treated as **unlimited** so existing free-tier orgs
        keep working; the UI surfaces 0 as "no cap configured".
        """
        budget = await self.org_budget(org_id)
        if budget <= 0:
            return
        spent = await self.mtd_spend(org_id)
        if spent >= budget:
            log.warning(
                "llm_budget_exceeded",
                org_id=str(org_id),
                mtd_spend_usd=round(spent, 4),
                budget_usd=round(budget, 4),
            )
            raise LLMBudgetExceededError(
                f"Monthly LLM budget reached: spent ${spent:.2f} of ${budget:.2f}. "
                "Increase the budget in Settings to resume."
            )
