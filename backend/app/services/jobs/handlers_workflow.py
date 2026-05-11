"""Workflow phase handlers — the durable replacement for the inline
``WorkflowService.execute()`` path.

A workflow run is decomposed into a chain of jobs:

    run.start  →  run.select  →  run.plan  →  (run.tailor) →
                  run.execute →  run.critique  →  run.publish

**Routing is owned by the runner, not by the handlers.** Each handler
calls ``svc.run_phase(...)`` and the runner returns a small
``PhaseResult`` dict describing what should happen next:

    {
      "done": True,             # terminal — stop the chain
      "next": "<phase_name>",   # enqueue ``run.<phase_name>``
      ...phase-specific extras...
    }

The handler reads those fields and routes accordingly. Specifically:

  * ``done=True``           — stop. Don't enqueue anything. The runner
                              has already flipped run.status to a
                              terminal state (SUCCEEDED, FAILED, etc.).
  * ``next="<phase>"``      — enqueue ``run.<phase>``. Used for the
                              forward chain (select→plan→…) AND for
                              reruns (critique→execute).
  * No ``done``, no ``next`` — also stop. Used by review escalation
                              (run.status=AWAITING_REVIEW) — the run
                              resumes later when a human approves via
                              the /reviews API which re-enqueues
                              run.publish directly.

The unconditional-hardcoded-next-phase pattern that lived here before
was the source of the May 11 2026 "no posts produced" silent failure:
every fast-fail in an upstream phase still triggered every downstream
phase, each operating on the empty state left by the previous one.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.services.jobs.handlers import HandlerContext, register_handler
from app.services.jobs.queue import DuplicateIdempotencyKey, PermanentError

log = get_logger(__name__)


def _wf_service(ctx: HandlerContext):
    """Returns the durable runner — the queue handlers always use the
    phase-aware path. Falls back to whatever ``workflow_service`` was
    wired (for legacy contexts) only if the runner is missing."""
    svc = ctx.services.get("durable_runner") or ctx.services.get("workflow_service")
    if svc is None:
        raise PermanentError("durable runner not available in worker context")
    return svc


async def _advance(
    ctx: HandlerContext, run_id: UUID, out: dict[str, Any], *,
    phase_label: str,
) -> dict[str, Any]:
    """Common post-phase routing. Reads ``out`` from the runner and:
      * stops if done
      * enqueues ``run.<next>`` if a next phase was named
      * stops silently otherwise (review escalation pattern)
    Returns the handler's own result dict for the queue's job.result."""
    if out.get("done"):
        return {"phase": phase_label, "stopped": True,
                **{k: v for k, v in out.items() if k != "done"}}

    next_phase = out.get("next")
    if not next_phase:
        # No "next" + no "done" = paused (typically AWAITING_REVIEW).
        # Don't enqueue anything; the review approval path will pick
        # the run back up.
        return {"phase": phase_label, "paused": True,
                **{k: v for k, v in out.items() if k != "next"}}

    # Rerun-aware idempotency: when critique sends us back to execute
    # for revision N, the idempotency key needs to be unique so the
    # queue doesn't dedupe against the prior execute job.
    rerun_count = out.get("rerun_count")
    ik = f"{next_phase}:{run_id}"
    if rerun_count:
        ik = f"{ik}:r{rerun_count}"

    try:
        await ctx.queue.enqueue(
            f"run.{next_phase}", ctx.job.org_id,
            {"run_id": str(run_id)}, run_id=run_id,
            idempotency_key=ik,
        )
    except DuplicateIdempotencyKey as exc:
        # A retry of the current job after the next-phase was already
        # enqueued — that's fine, the next phase will run / has run.
        log.info("phase_next_enqueue_deduped",
                 next=next_phase, existing=exc.existing_job_id)
    return {"phase": phase_label, "next": f"run.{next_phase}",
            **{k: v for k, v in out.items() if k not in ("done", "next")}}


@register_handler("run.start")
async def run_start(ctx: HandlerContext) -> dict[str, Any]:
    """Entrypoint — initialises a run and enqueues run.select.

    The run row may already exist if the API created it upfront (the
    standard path now — gives the workflow card immediate "queued"
    feedback). In that case we skip ``create_run_for_job`` and go
    straight to enqueueing the next phase. The legacy path (callers
    that enqueue run.start without a run_id in the payload) still
    works — we create the row here.
    """
    payload = ctx.job.payload
    svc = _wf_service(ctx)

    run_id_str = payload.get("run_id")
    if run_id_str:
        run_id = UUID(run_id_str)
    else:
        run = await svc.create_run_for_job(
            org_id=ctx.job.org_id,
            workflow_id=payload["workflow_id"],
            trigger_kind=payload.get("trigger_kind", "manual"),
            trigger_payload=payload.get("trigger_payload") or {},
            directive=payload.get("directive"),
            idempotency_key=ctx.job.idempotency_key,
        )
        run_id = run.id

    try:
        await ctx.queue.enqueue(
            "run.select", ctx.job.org_id,
            {"run_id": str(run_id)}, run_id=run_id,
            idempotency_key=f"select:{run_id}",
        )
    except DuplicateIdempotencyKey:
        pass
    return {"run_id": str(run_id), "next": "run.select"}


@register_handler("run.select")
async def run_select(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(
        org_id=ctx.job.org_id, run_id=run_id, phase="select",
    )
    return await _advance(ctx, run_id, out, phase_label="select")


@register_handler("run.plan")
async def run_plan(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(
        org_id=ctx.job.org_id, run_id=run_id, phase="plan",
    )
    return await _advance(ctx, run_id, out, phase_label="plan")


@register_handler("run.tailor")
async def run_tailor(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(
        org_id=ctx.job.org_id, run_id=run_id, phase="tailor",
    )
    return await _advance(ctx, run_id, out, phase_label="tailor")


@register_handler("run.execute")
async def run_execute(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(
        org_id=ctx.job.org_id, run_id=run_id, phase="execute",
    )
    return await _advance(ctx, run_id, out, phase_label="execute")


@register_handler("run.critique")
async def run_critique(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(
        org_id=ctx.job.org_id, run_id=run_id, phase="critique",
    )
    # The runner returns one of:
    #   * extras={"requires_review": True}   → no next, no done → paused
    #   * next="execute", rerun=True, rerun_count=N → revision loop
    #   * next="publish"                     → approved
    #   * done=True (rerun_exhausted, aborted, or rejected)
    # _advance() handles all of them uniformly.
    return await _advance(ctx, run_id, out, phase_label="critique")


@register_handler("run.publish")
async def run_publish(ctx: HandlerContext) -> dict[str, Any]:
    svc = _wf_service(ctx)
    run_id = UUID(ctx.job.payload["run_id"])
    out = await svc.run_phase(
        org_id=ctx.job.org_id, run_id=run_id, phase="publish",
    )
    # Publish is terminal (done=True) — but it also returns post_ids
    # so we can schedule engagement-fetch follow-ups (Pillar 3).
    for delay in (3600, 86400):
        for post_id in out.get("post_ids", []) or []:
            try:
                await ctx.queue.enqueue(
                    "engagement.fetch", ctx.job.org_id,
                    {"post_id": post_id},
                    idempotency_key=f"eng:{post_id}:{delay}",
                    scheduled_for=_in_seconds(delay),
                    priority=200,
                )
            except DuplicateIdempotencyKey:
                pass
    return await _advance(ctx, run_id, out, phase_label="publish")


def _in_seconds(delay: int):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(seconds=delay)
