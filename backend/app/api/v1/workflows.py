from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import current_user, get_audit_log_service, get_workflow_service
from app.core.security import Principal
from app.services.audit_log import AuditLogService
from app.domain.entities.workflow import WorkflowConfig
from app.domain.value_objects.ids import OrgId, PlatformId, SourceId, WorkflowId
from app.domain.value_objects.schedule import Schedule, ScheduleKind
from app.domain.value_objects.targeting import TargetSelector
from app.schemas.workflows import (
    TargetSelectorIn,
    WorkflowConfigIn,
    WorkflowCreate,
    WorkflowOut,
    WorkflowRunOut,
)
from app.services.workflow_service import WorkflowService

router = APIRouter()


def _to_config(c: WorkflowConfigIn) -> WorkflowConfig:
    # If the caller picked a provider but didn't supply a model, fall back
    # to that provider's plugin-declared `default_model` rather than the
    # WorkflowConfig dataclass default (which is Claude-specific). Without
    # this, a workflow with provider=openai would silently inherit
    # model=claude-sonnet-4-6 and crash at run time.
    model = c.llm_model
    if c.llm_provider and not model:
        model = _provider_default_model(c.llm_provider) or "claude-sonnet-4-6"
    return WorkflowConfig(
        tone=c.tone, audience=c.audience, voice_guide=c.voice_guide,
        max_revisions=c.max_revisions, quality_threshold=c.quality_threshold,
        low_quality_threshold=c.low_quality_threshold,
        require_human_approval=c.require_human_approval,
        llm_provider=c.llm_provider, llm_model=model,
        use_brand_voice=c.use_brand_voice, brand_voice_top_k=c.brand_voice_top_k,
        compliance_profile=(
            c.compliance_profile if (c.compliance_profile or "").lower() not in ("", "none")
            else None
        ),
        custom_system_prompt=(c.custom_system_prompt or "").strip() or None,
        extra=c.extra,
    )


def _provider_default_model(provider: str) -> str | None:
    """Lookup the LLM plugin's class-level ``default_model`` so a workflow
    that specified a provider but no model gets a sensible per-provider
    default (e.g. openai → gpt-4o, gemini → gemini-1.5-pro)."""
    try:
        from app.plugins.registry import PluginKind, get_global_registry
        entry = get_global_registry().get(PluginKind.LLM, provider)
        return getattr(entry.cls, "default_model", None) or None
    except Exception:                                                   # noqa: BLE001
        return None


def _to_schedule(s) -> Schedule:
    return Schedule(
        kind=ScheduleKind(s.kind), cron=s.cron,
        interval_minutes=s.interval_minutes, run_at=s.run_at, timezone=s.timezone,
    )


def _to_selector(s: TargetSelectorIn) -> TargetSelector:
    return TargetSelector(
        explicit_platform_ids=tuple(PlatformId(p) for p in s.explicit_platform_ids),
        all_of_platforms=tuple(s.all_of_platforms),
        by_handle=tuple(s.by_handle),
        tagged=tuple(s.tagged),
        exclude_platform_ids=tuple(PlatformId(p) for p in s.exclude_platform_ids),
        exclude_plugins=tuple(s.exclude_plugins),
    )


def _selector_out(sel) -> TargetSelectorIn:
    return TargetSelectorIn(
        explicit_platform_ids=[UUID(str(p)) for p in sel.explicit_platform_ids],
        all_of_platforms=list(sel.all_of_platforms),
        by_handle=list(sel.by_handle),
        tagged=list(sel.tagged),
        exclude_platform_ids=[UUID(str(p)) for p in sel.exclude_platform_ids],
        exclude_plugins=list(sel.exclude_plugins),
    )


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> list[WorkflowOut]:
    items = await svc.list(OrgId(UUID(user.org_id)))
    return [_to_out(w) for w in items]


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowCreate,
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    wf = await svc.create(
        org_id=OrgId(UUID(user.org_id)),
        name=body.name,
        description=body.description,
        source_ids=[SourceId(s) for s in body.source_ids],
        platform_ids=[PlatformId(p) for p in body.platform_ids],
        config=_to_config(body.config),
        schedule=_to_schedule(body.schedule),
        target_selector=_to_selector(body.target_selector),
    )
    return _to_out(wf)


class WorkflowUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None
    source_ids: list[UUID] | None = None
    platform_ids: list[UUID] | None = None
    config: WorkflowConfigIn | None = None
    schedule: dict | None = None
    target_selector: TargetSelectorIn | None = None


@router.patch("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: UUID,
    body: WorkflowUpdateBody,
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")

    # Map any provided fields onto domain types
    schedule = None
    if body.schedule is not None:
        class _S:                                                # tiny shim
            kind = body.schedule.get("kind", "manual")
            cron = body.schedule.get("cron")
            interval_minutes = body.schedule.get("interval_minutes")
            run_at = body.schedule.get("run_at")
            timezone = body.schedule.get("timezone", "UTC")
        schedule = _to_schedule(_S())

    try:
        wf = await svc.update(
            org_id=OrgId(UUID(user.org_id)),
            workflow_id=WorkflowId(workflow_id),
            name=body.name,
            description=body.description,
            source_ids=[SourceId(s) for s in body.source_ids] if body.source_ids is not None else None,
            platform_ids=[PlatformId(p) for p in body.platform_ids] if body.platform_ids is not None else None,
            config=_to_config(body.config) if body.config is not None else None,
            schedule=schedule,
            target_selector=_to_selector(body.target_selector) if body.target_selector is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_out(wf)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: UUID,
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> None:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    await svc.delete(OrgId(UUID(user.org_id)), WorkflowId(workflow_id))
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="workflow.delete",
            resource_type="workflow",
            resource_id=workflow_id,
        )


@router.post("/{workflow_id}/activate", response_model=WorkflowOut)
async def activate(
    workflow_id: UUID,
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> WorkflowOut:
    try:
        wf = await svc.activate(OrgId(UUID(user.org_id)), WorkflowId(workflow_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="workflow.activate",
            resource_type="workflow",
            resource_id=wf.id,
            after={"name": wf.name, "status": wf.status.value},
        )
    return _to_out(wf)


@router.post("/{workflow_id}/pause", response_model=WorkflowOut)
async def pause(
    workflow_id: UUID,
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> WorkflowOut:
    try:
        wf = await svc.pause(OrgId(UUID(user.org_id)), WorkflowId(workflow_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="workflow.pause",
            resource_type="workflow",
            resource_id=wf.id,
            after={"name": wf.name, "status": wf.status.value},
        )
    return _to_out(wf)


@router.post("/{workflow_id}/duplicate", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def duplicate(
    workflow_id: UUID,
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    try:
        wf = await svc.duplicate(OrgId(UUID(user.org_id)), WorkflowId(workflow_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_out(wf)


@router.get("/{workflow_id}/runs")
async def list_runs(
    workflow_id: UUID,
    limit: int = 20,
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> list[dict]:
    """Recent runs for a workflow, newest first, with their agent traces."""
    org_id = OrgId(UUID(user.org_id))
    runs = await svc.run_repo.list_for_workflow(org_id, WorkflowId(workflow_id))
    runs = sorted(runs, key=lambda r: r.started_at, reverse=True)[:limit]
    return [{
        "id": str(r.id),
        "workflow_id": str(r.workflow_id),
        "status": r.status.value,
        "directive": r.directive,
        "initiator": r.initiator,
        "revision_count": r.revision_count,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "error": r.error,
        "trace": [
            {"agent": e.agent, "event": e.event,
             "ts": e.occurred_at.isoformat(), **e.payload}
            for e in r.trace
        ],
    } for r in runs]


@router.get("/{workflow_id}/posts")
async def list_workflow_posts(
    workflow_id: UUID,
    limit: int = 50,
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> list[dict]:
    """All posts produced by this workflow, newest first."""
    org_id = OrgId(UUID(user.org_id))
    posts = await svc.post_repo.list(org_id)
    posts = [p for p in posts if str(p.workflow_id) == str(workflow_id)]
    posts.sort(key=lambda p: p.created_at, reverse=True)
    return [{
        "id": str(p.id),
        "platform_id": str(p.platform_id),
        "text": p.text,
        "hashtags": [h.value for h in p.hashtags],
        "status": p.status.value,
        "scheduled_for": p.scheduled_for.isoformat() if p.scheduled_for else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "external_post_id": p.external_post_id,
        "error": p.error,
        "created_at": p.created_at.isoformat(),
    } for p in posts[:limit]]


@router.post("/{workflow_id}/run", response_model=WorkflowRunOut)
async def run_now(
    workflow_id: UUID,
    background: BackgroundTasks,
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> WorkflowRunOut:
    """Run the workflow inline (returns trace). For prod, dispatch via Celery."""
    run = await svc.run(OrgId(UUID(user.org_id)), WorkflowId(workflow_id))
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="workflow.run",
            resource_type="workflow",
            resource_id=workflow_id,
            after={
                "run_id": str(run.id),
                "status": run.status.value,
                "initiator": run.initiator,
            },
        )
    return WorkflowRunOut(
        id=run.id, workflow_id=run.workflow_id, status=run.status.value,
        revision_count=run.revision_count, started_at=run.started_at,
        finished_at=run.finished_at, error=run.error,
        trace=[{"agent": e.agent, "event": e.event, "ts": e.occurred_at.isoformat(),
                **e.payload} for e in run.trace],
    )


def _to_out(wf) -> WorkflowOut:
    return WorkflowOut(
        id=wf.id, name=wf.name, description=wf.description,
        status=wf.status.value,
        source_ids=list(wf.source_ids), platform_ids=list(wf.platform_ids),
        config=WorkflowConfigIn(
            tone=wf.config.tone, audience=wf.config.audience,
            voice_guide=wf.config.voice_guide, max_revisions=wf.config.max_revisions,
            quality_threshold=wf.config.quality_threshold,
            low_quality_threshold=wf.config.low_quality_threshold,
            require_human_approval=wf.config.require_human_approval,
            llm_provider=wf.config.llm_provider, llm_model=wf.config.llm_model,
            extra=wf.config.extra,
        ),
        schedule={
            "kind": wf.schedule.kind.value, "cron": wf.schedule.cron,
            "interval_minutes": wf.schedule.interval_minutes,
            "run_at": wf.schedule.run_at, "timezone": wf.schedule.timezone,
        },
        target_selector=_selector_out(wf.target_selector),
        created_at=wf.created_at, updated_at=wf.updated_at,
    )
