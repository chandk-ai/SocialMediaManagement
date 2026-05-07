"""CampaignService — orchestrates multi-step campaigns by walking each
campaign's `due_steps()` and dispatching them as targeted workflow runs.

The actual generation/publish work stays inside `WorkflowService`; the
campaign layer is just a scheduler + creative-brief enrichment overlay.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.core.logging import get_logger
from app.domain.entities.campaign import (
    Campaign,
    CampaignKind,
    CampaignStatus,
    CampaignStep,
    CampaignStepStatus,
)
from app.domain.entities.workflow_run import WorkflowRun
from app.domain.value_objects.ids import (
    CampaignId,
    CampaignStepId,
    OrgId,
    PlatformId,
    WorkflowId,
)
from app.domain.value_objects.targeting import TargetSelector
from app.repositories.ports import (
    CampaignRepository,
    WorkflowRepository,
)
from app.services.workflow_service import WorkflowService

log = get_logger(__name__)


class CampaignService:
    """Pure orchestration service. Owns no LLM — defers generation to
    `WorkflowService.run_now`."""

    def __init__(
        self,
        repo: CampaignRepository,
        wf_repo: WorkflowRepository,
        wf_service: WorkflowService,
    ) -> None:
        self.repo = repo
        self.wf_repo = wf_repo
        self.wf_service = wf_service

    # ── CRUD ──────────────────────────────────────────────────────────
    async def create(
        self,
        *,
        org_id: OrgId,
        name: str,
        description: str,
        kind: CampaignKind,
        starts_at: datetime,
        ends_at: datetime,
        steps: list[CampaignStep] | None = None,
        target_platform_ids: list[PlatformId] | None = None,
        goal: str | None = None,
        success_metric: str | None = None,
        extras: dict | None = None,
    ) -> Campaign:
        c = Campaign.create(
            org_id=org_id,
            name=name,
            description=description,
            kind=kind,
            starts_at=starts_at,
            ends_at=ends_at,
            steps=steps,
            target_platform_ids=target_platform_ids,
            goal=goal,
            success_metric=success_metric,
            extras=extras,
        )
        return await self.repo.add(c)

    async def list(
        self, org_id: OrgId, *, status: str | None = None,
    ) -> list[Campaign]:
        return await self.repo.list(org_id, status=status)

    async def get(self, org_id: OrgId, campaign_id: CampaignId) -> Campaign:
        c = await self.repo.get(org_id, campaign_id)
        if not c:
            raise ValueError("campaign not found")
        return c

    async def update_steps(
        self,
        *,
        org_id: OrgId,
        campaign_id: CampaignId,
        steps: list[CampaignStep],
    ) -> Campaign:
        c = await self.get(org_id, campaign_id)
        c.steps = list(steps)
        c.touch()
        return await self.repo.update(c)

    async def add_step(
        self,
        *,
        org_id: OrgId,
        campaign_id: CampaignId,
        step: CampaignStep,
    ) -> Campaign:
        c = await self.get(org_id, campaign_id)
        c.steps.append(step)
        c.touch()
        return await self.repo.update(c)

    async def activate(self, org_id: OrgId, campaign_id: CampaignId) -> Campaign:
        c = await self.get(org_id, campaign_id)
        c.activate()
        return await self.repo.update(c)

    async def pause(self, org_id: OrgId, campaign_id: CampaignId) -> Campaign:
        c = await self.get(org_id, campaign_id)
        c.pause()
        return await self.repo.update(c)

    async def cancel(
        self, org_id: OrgId, campaign_id: CampaignId, reason: str | None = None,
    ) -> Campaign:
        c = await self.get(org_id, campaign_id)
        c.cancel(reason)
        return await self.repo.update(c)

    async def delete(self, org_id: OrgId, campaign_id: CampaignId) -> None:
        await self.repo.delete(org_id, campaign_id)

    # ── runtime ───────────────────────────────────────────────────────
    async def list_due(self, now: datetime | None = None) -> list[Campaign]:
        return await self.repo.list_due(now or datetime.utcnow())

    async def execute_due_steps(
        self,
        *,
        now: datetime | None = None,
        max_per_campaign: int = 5,
    ) -> list[tuple[CampaignId, CampaignStepId, str]]:
        """Find every due step across every active campaign and dispatch.

        Returns a list of (campaign_id, step_id, outcome) for logging.
        Outcome is one of: "started", "completed", "failed", "missing_workflow".
        """
        now = now or datetime.utcnow()
        outcomes: list[tuple[CampaignId, CampaignStepId, str]] = []
        for campaign in await self.repo.list_due(now):
            if campaign.status is CampaignStatus.SCHEDULED:
                campaign.start()
            count = 0
            for step in campaign.due_steps(now):
                if count >= max_per_campaign:
                    break
                count += 1
                outcome = await self._execute_step(campaign, step)
                outcomes.append((campaign.id, step.id, outcome))
            campaign.evaluate_completion()
            await self.repo.update(campaign)
        return outcomes

    async def _execute_step(
        self, campaign: Campaign, step: CampaignStep,
    ) -> str:
        wf = await self.wf_repo.get(campaign.org_id, step.workflow_id)
        if not wf:
            step.mark_failed("workflow not found")
            return "missing_workflow"
        step.mark_running()
        try:
            run = await self.wf_service.run_now(
                org_id=campaign.org_id,
                workflow_id=step.workflow_id,
                directive=self._compose_directive(campaign, step),
                target_override=step.target_selector
                if not step.target_selector.is_empty()
                else None,
            )
            run_id = getattr(run, "id", None) if run else None
            step.mark_completed(run_id=run_id)
            return "completed" if run else "started"
        except Exception as exc:                        # noqa: BLE001
            log.exception(
                "campaign_step_failed",
                campaign_id=str(campaign.id),
                step_id=str(step.id),
                error=str(exc),
            )
            step.mark_failed(str(exc))
            return "failed"

    @staticmethod
    def _compose_directive(campaign: Campaign, step: CampaignStep) -> str:
        """Glue the campaign-wide goal + the step-specific brief into a
        single natural-language directive that the Planner consumes."""
        bits: list[str] = []
        if campaign.kind:
            bits.append(f"Campaign type: {campaign.kind.value}.")
        if campaign.goal:
            bits.append(f"Campaign goal: {campaign.goal}.")
        if step.title:
            bits.append(f"Step: {step.title}.")
        if step.directive:
            bits.append(step.directive)
        if step.depends_on:
            bits.append(
                "Build on the prior step in this campaign — keep the narrative thread."
            )
        return " ".join(bits).strip()


def select_workflow_ids(steps: Iterable[CampaignStep]) -> set[WorkflowId]:
    """Convenience helper used by the WhatsApp/Telegram triggers when they
    show "this run is part of campaign X" context to the reviewer."""
    return {s.workflow_id for s in steps}
