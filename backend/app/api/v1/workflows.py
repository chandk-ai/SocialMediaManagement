from __future__ import annotations

import time as _time
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
    ScheduleIn,
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
        selection_strategy=(c.selection_strategy or "freshness").strip() or "freshness",
        selection_config=dict(c.selection_config or {}),
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
    # ``schedule`` MUST be the typed ScheduleIn (see import below) — not
    # a raw dict — because Pydantic is what coerces the inbound
    # ISO-string ``run_at`` into a real ``datetime``. The shim-based
    # approach used previously left ``run_at`` as a string, which
    # broke ``Schedule(run_at=...).isoformat()`` downstream with the
    # exact ``'str' object has no attribute 'isoformat'`` we shipped.
    name: str | None = None
    description: str | None = None
    source_ids: list[UUID] | None = None
    platform_ids: list[UUID] | None = None
    config: WorkflowConfigIn | None = None
    schedule: ScheduleIn | None = None
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

    # Map any provided fields onto domain types. ``ScheduleIn`` has
    # ``run_at: datetime`` so Pydantic has already parsed the inbound
    # ISO string into a real datetime by the time we touch it here.
    schedule = _to_schedule(body.schedule) if body.schedule is not None else None

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
    """Recent runs for a workflow, newest first, with their agent traces.

    Includes runs that haven't started a phase yet (``status='queued'``,
    ``started_at=None``) — these are the runs that have just been
    enqueued from the API but not yet claimed by a worker. Surfacing
    them is what gives the workflow card immediate "Queued…" feedback
    after a Run Now click."""
    from datetime import datetime, timezone
    org_id = OrgId(UUID(user.org_id))
    runs = await svc.run_repo.list_for_workflow(org_id, WorkflowId(workflow_id))
    # Repo already returns ordered by created_at desc, which is
    # always-non-null and matches the "newest first" intent. The
    # repo doesn't return ``created_at`` on the domain entity, so
    # don't try to re-sort here on a field that may be None.
    runs = runs[:limit]
    return [{
        "id": str(r.id),
        "workflow_id": str(r.workflow_id),
        "status": r.status.value,
        "directive": r.directive,
        "initiator": r.initiator,
        "revision_count": r.revision_count,
        # Both timestamps can be None on queued/awaiting-review runs.
        "started_at": r.started_at.isoformat() if r.started_at else None,
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


@router.post("/{workflow_id}/run-durable")
async def run_durable(
    workflow_id: UUID,
    user: Principal = Depends(current_user),
) -> dict:
    """Enqueue the workflow run on the durable queue. Returns
    immediately with the job_id; the run progresses through phases as
    the worker pool consumes the queue. Use ``/jobs?run_id=…`` to track
    progress, or open the run detail page for the live trace.

    Pillar 1's resilient path. Prefer this over ``/run`` for production
    workloads — the inline ``/run`` endpoint blocks the API thread and
    has no built-in retry."""
    from app.api.deps import get_job_queue, get_tenant_rate_limiter
    queue = get_job_queue()
    limiter = get_tenant_rate_limiter()
    if not await limiter.try_consume(user.org_id, cost=1.0):
        raise HTTPException(
            status_code=429,
            detail="tenant rate limit exceeded — try again in a few seconds",
        )
    try:
        job = await queue.enqueue(
            "run.start", user.org_id,
            {"workflow_id": str(workflow_id), "trigger_kind": "manual"},
            # Minute-bucketed key so a fast double-click is de-duped,
            # but a deliberate re-run a minute later still goes through.
            # Principal exposes ``subject`` (Supabase JWT sub claim), not
            # ``id`` — using subject here so different teammates can each
            # trigger the same workflow in the same minute distinctly.
            idempotency_key=f"start:{workflow_id}:{user.subject}:{int(_time.time() // 60)}",
        )
    except Exception as exc:                                         # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "job_id": job.id, "kind": job.kind, "status": job.status.value,
        "scheduled_for": job.scheduled_for.isoformat(),
    }


@router.post("/{workflow_id}/run")
async def run_now(
    workflow_id: UUID,
    background: BackgroundTasks,
    mode: str = "auto",
    user: Principal = Depends(current_user),
    svc: WorkflowService = Depends(get_workflow_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    """Trigger a workflow run.

    ``mode`` selects the path:
      * ``auto`` (default) — durable when the jobs backend is Postgres,
        inline when it's the in-memory dev backend. Recommended for
        all clients; the response shape is the same in both cases.
      * ``durable``         — force the Postgres-backed jobs path. 429s
        when the tenant rate-limit bucket is empty.
      * ``inline``          — force the legacy synchronous path. Useful
        for tests and for ad-hoc debugging where you want the trace
        to come back in the response body.

    Response shape:
      ``{"mode": "durable"|"inline", "run_id": str|null, "job_id": str|null,
        "status": str, "trace": [...]?}``

    For durable mode, ``run_id`` is null until a worker picks up
    ``run.start`` and creates the run row; clients should poll
    ``GET /jobs/{job_id}`` then ``GET /workflows/{wf}/runs/{run_id}``."""
    from app.api.deps import (
        get_job_queue, get_settings, get_tenant_rate_limiter,
    )

    settings = get_settings()
    backend_supports_durable = (
        settings.resolved_persistence_backend() == "supabase"
    )

    # Decide path.
    chosen = mode.lower()
    if chosen == "auto":
        chosen = "durable" if backend_supports_durable else "inline"
    if chosen == "durable" and not backend_supports_durable:
        # Caller forced durable on memory backend — refuse rather than
        # silently fall back, so they fix their config.
        raise HTTPException(
            status_code=503,
            detail="durable mode requires the Postgres backend; "
                   "switch PERSISTENCE_BACKEND or call with mode=inline",
        )

    if chosen == "durable":
        from app.api.deps import get_durable_runner
        from app.services.jobs.queue import DuplicateIdempotencyKey
        queue = get_job_queue()
        limiter = get_tenant_rate_limiter()
        runner = get_durable_runner()
        if not await limiter.try_consume(user.org_id, cost=1.0):
            raise HTTPException(
                status_code=429,
                detail="tenant rate limit exceeded — try again in a few seconds",
            )

        # Minute-bucketed idempotency: a fast double-click within the
        # same minute returns the original job's run_id; a deliberate
        # click 60s later creates a fresh run.
        ik = f"start:{workflow_id}:{user.subject}:{int(_time.time() // 60)}"

        # We create the run row FIRST so the workflow card sees it
        # immediately (pre-fix: row was created lazily inside the
        # worker, leaving a multi-second "did Run Now do anything?"
        # gap). If the job enqueue then de-dupes (existing job from a
        # prior click in the same minute), we look up its run_id and
        # return THAT instead of leaving an orphan row.
        try:
            run = await runner.create_run_for_job(
                org_id=user.org_id, workflow_id=str(workflow_id),
                trigger_kind="manual", idempotency_key=ik,
            )
        except Exception as exc:                                     # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        try:
            job = await queue.enqueue(
                "run.start", user.org_id,
                # Pass the pre-created run_id so the worker's run.start
                # handler skips create_run_for_job and goes straight to
                # enqueueing run.select against the existing row.
                {"workflow_id": str(workflow_id),
                 "run_id": str(run.id),
                 "trigger_kind": "manual"},
                run_id=run.id, idempotency_key=ik,
            )
        except DuplicateIdempotencyKey as exc:
            # Existing job for this minute-bucket — look up its
            # run_id and return that one. The freshly-created run row
            # is now orphaned; flag it FAILED with a clear reason so
            # the runs list doesn't accumulate untouchable junk.
            existing_job = await queue.get(exc.existing_job_id)
            existing_run_id = existing_job.run_id if existing_job else None
            try:
                run.error = "superseded by concurrent click (same minute bucket)"
                from app.domain.entities.workflow_run import RunStatus
                run.transition(RunStatus.CANCELLED)
                await runner.run_repo.update(run)
            except Exception:                                        # noqa: BLE001
                pass
            return {
                "mode": "durable",
                "run_id": existing_run_id or str(run.id),
                "job_id": exc.existing_job_id,
                "status": "queued",
                "scheduled_for": None,
                "deduplicated": True,
            }
        except Exception as exc:                                     # noqa: BLE001
            raise HTTPException(status_code=500,
                                 detail=str(exc)) from exc

        if audit is not None:
            await audit.record(
                org_id=user.org_id, action="workflow.run.enqueued",
                resource_type="workflow", resource_id=workflow_id,
                after={"job_id": job.id, "run_id": str(run.id),
                        "mode": "durable"},
            )
        return {
            "mode": "durable",
            "run_id": str(run.id), "job_id": job.id,
            "status": "queued",
            "scheduled_for": job.scheduled_for.isoformat(),
        }

    # Inline path — preserved for tests + memory mode.
    run = await svc.run(OrgId(UUID(user.org_id)), WorkflowId(workflow_id))
    if audit is not None:
        await audit.record(
            org_id=user.org_id, action="workflow.run",
            resource_type="workflow", resource_id=workflow_id,
            after={"run_id": str(run.id), "status": run.status.value,
                    "mode": "inline", "initiator": run.initiator},
        )
    return {
        "mode": "inline",
        "run_id": str(run.id), "job_id": None,
        "status": run.status.value,
        "revision_count": run.revision_count,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
        "trace": [
            {"agent": e.agent, "event": e.event,
             "ts": e.occurred_at.isoformat(), **e.payload}
            for e in run.trace
        ],
    }


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
