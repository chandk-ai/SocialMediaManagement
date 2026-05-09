"""Workflow phase handlers — the durable replacement for the inline
``WorkflowService.execute()`` path.

A workflow run is decomposed into a chain of jobs:

    run.select   →  run.plan   →  run.tailor   →  run.execute
                 →  run.critique →  run.publish

Each handler:
  1. Loads the run + workflow.
  2. Runs its phase via the existing service code.
  3. Writes phase outputs back to the run row.
  4. Enqueues the next phase (with the same run_id) — unless the phase
     decided to stop early (e.g. selection found nothing).

The handler is responsible for advancing run.status. Failures bubble up
to the worker which schedules a retry; if a phase exhausts its
retries, ``run.status='failed'`` is set inside the dead-letter
finalizer (handlers_runtime.py for now lives here).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.services.jobs.handlers import HandlerContext, register_handler
from app.services.jobs.queue import PermanentError

log = get_logger(__name__)


def _wf_service(ctx: HandlerContext):
    """Returns the durable runner — the queue handlers always use the
    phase-aware path. Falls back to whatever ``workflow_service`` was
    wired (for legacy contexts) only if the runner is missing."""
    svc = ctx.services.get("durable_runner") or ctx.services.get("workflow_service")
    if svc is None:
        raise PermanentError("durable runner not available in worker context")
    return svc


@register_handler("run.start")
async def run_start(ctx: HandlerContext) -> dict[str, Any]:
    """Entrypoint — accepts (workflow_id, trigger_payload, directive),
    creates the run row, and enqueues run.select. The API enqueues
    this handler instead of calling workflow_service.execute() directly,
    so the API never blocks on a long run."""
    payload = ctx.job.payload
    svc = _wf_service(ctx)
    run = await svc.create_run_for_job(
        org_id=ctx.job.org_id,
        workflow_id=payload["workflow_id"],
        trigger_kind=payload.get("trigger_kind", "manual"),
        trigger_payload=payload.get("trigger_payload") or {},
        directive=payload.get("directive"),
        idempotency_key=ctx.job.idempotency_key,
    )
    await ctx.queue.enqueue(
        "run.select", ctx.job.org_id,
        {"run_id": str(run.id)}, run_id=run.id,
        idempotency_key=f"sel:{run.id}",
    )
    return {"run_id": str(run.id), "next": "run.select"}


@register_handler("run.select")
async def run_select(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(run_id=run_id, phase="select")
    if out.get("done"):
        return {"phase": "select", "stopped": True, "reason": out.get("reason")}
    await ctx.queue.enqueue(
        "run.plan", ctx.job.org_id,
        {"run_id": str(run_id)}, run_id=run_id,
        idempotency_key=f"plan:{run_id}",
    )
    return {"phase": "select", "selected": out.get("count", 0)}


@register_handler("run.plan")
async def run_plan(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(run_id=run_id, phase="plan")
    next_kind = "run.tailor" if out.get("multi_platform") else "run.execute"
    await ctx.queue.enqueue(
        next_kind, ctx.job.org_id,
        {"run_id": str(run_id)}, run_id=run_id,
        idempotency_key=f"{next_kind.split('.')[1]}:{run_id}",
    )
    return {"phase": "plan", "next": next_kind}


@register_handler("run.tailor")
async def run_tailor(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(run_id=run_id, phase="tailor")
    await ctx.queue.enqueue(
        "run.execute", ctx.job.org_id,
        {"run_id": str(run_id)}, run_id=run_id,
        idempotency_key=f"exec:{run_id}",
    )
    return {"phase": "tailor", "variants": out.get("variants", 0)}


@register_handler("run.execute")
async def run_execute(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(run_id=run_id, phase="execute")
    await ctx.queue.enqueue(
        "run.critique", ctx.job.org_id,
        {"run_id": str(run_id)}, run_id=run_id,
        idempotency_key=f"crit:{run_id}",
    )
    return {"phase": "execute", "drafts": out.get("count", 0)}


@register_handler("run.critique")
async def run_critique(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(run_id=run_id, phase="critique")
    if out.get("requires_review"):
        # Hand off to review — no further phase enqueued; reviewers
        # eventually call /reviews/.../approve which enqueues run.publish.
        return {"phase": "critique", "review_pending": True}
    if out.get("rerun"):
        # Critique failed gates and asked for a rerun — go back to plan.
        await ctx.queue.enqueue(
            "run.plan", ctx.job.org_id,
            {"run_id": str(run_id), "rerun": True},
            run_id=run_id,
            idempotency_key=f"plan:{run_id}:r{out.get('rerun_count',1)}",
        )
        return {"phase": "critique", "rerunning": True}
    await ctx.queue.enqueue(
        "run.publish", ctx.job.org_id,
        {"run_id": str(run_id)}, run_id=run_id,
        idempotency_key=f"pub:{run_id}",
    )
    return {"phase": "critique", "approved": True}


@register_handler("run.publish")
async def run_publish(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(run_id=run_id, phase="publish")
    # Schedule engagement-fetch follow-ups for each post — in 60min, 24h.
    for delay in (3600, 86400):
        for post_id in out.get("post_ids", []):
            await ctx.queue.enqueue(
                "engagement.fetch", ctx.job.org_id,
                {"post_id": post_id},
                idempotency_key=f"eng:{post_id}:{delay}",
                scheduled_for=_in_seconds(delay),
                priority=200,
            )
    return {"phase": "publish", "posts": len(out.get("post_ids", []))}


def _in_seconds(delay: int):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(seconds=delay)
