"""LLM usage / spend endpoints — month-to-date dollar spend per org and a
recent-completion ledger. The Settings page reads this to render the budget
bar; the budget guard reads the same data at agent runtime to decide whether
to allow the next completion."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_llm_usage_service
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId
from app.services.llm_usage import LlmUsageService

router = APIRouter()


class BudgetBody(BaseModel):
    monthly_llm_budget_usd: float = Field(ge=0)


def _require(svc: LlmUsageService | None) -> LlmUsageService:
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="LLM usage tracking not available — backend is in memory mode.",
        )
    return svc


@router.get("/llm-usage")
async def get_usage(
    user: Principal = Depends(current_user),
    svc: LlmUsageService | None = Depends(get_llm_usage_service),
) -> dict:
    """Return MTD spend, org budget, remaining USD, and over-budget flag."""
    s = _require(svc)
    org = OrgId(UUID(user.org_id))
    summary = await s.summary(org)
    return {
        "billing_month": summary.billing_month,
        "mtd_spend_usd": summary.mtd_spend_usd,
        "budget_usd": summary.budget_usd,
        "remaining_usd": summary.remaining_usd,
        "over_budget": summary.over_budget,
    }


@router.get("/llm-usage/recent")
async def get_recent(
    limit: int = 50,
    user: Principal = Depends(current_user),
    svc: LlmUsageService | None = Depends(get_llm_usage_service),
) -> dict:
    """Last N completions for the org — for the audit / debug table."""
    s = _require(svc)
    org = OrgId(UUID(user.org_id))
    rows = await s.recent(org, limit=max(1, min(int(limit), 200)))
    return {
        "rows": [
            {
                "occurred_at": r.occurred_at,
                "provider": r.provider,
                "model": r.model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": r.cost_usd,
                "context": r.context,
            }
            for r in rows
        ],
    }


@router.put("/llm-usage/budget")
async def set_budget(
    body: BudgetBody,
    user: Principal = Depends(current_user),
    svc: LlmUsageService | None = Depends(get_llm_usage_service),
) -> dict:
    """Set the org's monthly LLM budget. 0 = unlimited (the guard becomes
    a no-op)."""
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    s = _require(svc)
    # Reuse the service's session factory rather than opening another engine.
    from sqlalchemy import text
    async with s._sm() as sess:                                          # noqa: SLF001
        await sess.execute(
            text(
                "UPDATE smms.organizations "
                "SET monthly_llm_budget_usd = :b, updated_at = now() "
                "WHERE id = :org_id"
            ),
            {"b": float(body.monthly_llm_budget_usd), "org_id": UUID(user.org_id)},
        )
        await sess.commit()
    return {"ok": True, "monthly_llm_budget_usd": body.monthly_llm_budget_usd}
