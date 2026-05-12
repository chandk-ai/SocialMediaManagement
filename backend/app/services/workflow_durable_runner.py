"""DurableWorkflowRunner — phase-by-phase orchestration for the jobs queue.

This is the **resilient** counterpart to ``WorkflowService.execute()``. The
inline path is fine for short, dev-mode runs; this one is what production
uses. Each phase is a separate job, each phase persists its output to
``run.metadata`` so a retry resumes from where it left off.

Phase contract — each ``_phase_*`` method:
  1. Loads run + workflow.
  2. Reads the prior phase's output from ``run.metadata`` (if applicable).
  3. Performs its work (selection / planning / tailoring / executing /
     critiquing / publishing).
  4. Writes its own output to ``run.metadata`` and persists the run.
  5. Returns a small dict the queue handler uses to decide what to
     enqueue next (or whether to stop).

Why a separate class instead of growing WorkflowService:
  * The existing inline path is large and battle-tested; this one
    starts as a thin re-implementation of the orchestrator loop using
    the same building blocks (agent factory, source loader, selection
    layer, target resolver) but composed phase-wise.
  * Pillars 4 (RAG injection), 5 (Tailor), 2 (OTel spans) all hook
    into phase boundaries — keeping them in one focused class makes
    that wiring obvious.
  * Co-existence is intentional: the API endpoint ``POST /workflows/
    {id}/run-durable`` enqueues ``run.start``; legacy ``POST /workflows/
    {id}/execute`` keeps the inline path. Both eventually emit Posts.

Failure semantics:
  * Transient (network, 5xx, LLM blip) → exception bubbles up; worker
    schedules a retry with exponential backoff. The phase-state in
    ``run.metadata`` is preserved so the retry doesn't redo prior work.
  * Permanent (compliance reject, missing required config) → raise
    ``PermanentError`` from the handler; worker DLQs the job and the
    runner flips ``run.status='failed'`` with a meaningful error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.adapters.platforms.base import PostPayload
from app.agents.base import AgentDecision, AgentState
from app.agents.factory import build_orchestrator
from app.agents.source_loader import load_items
from app.core.logging import get_logger
from app.core.metrics import M
from app.core.tracing import trace_span
from app.domain.entities.post import Post, PostStatus
from app.domain.entities.workflow_run import (
    AgentTraceEvent, RunStatus, WorkflowRun,
)
from app.domain.value_objects.content import (
    ContentPlan, DraftPost, Hashtag, MediaAsset, MediaKind, PostBlueprint,
)
from app.domain.value_objects.ids import OrgId, RunId, WorkflowId
from app.domain.value_objects.selection import ItemMode
from app.plugins.registry import PluginKind
from app.services.circuit_breaker import CircuitBreaker, CircuitOpen
from app.services.jobs.queue import PermanentError
from app.services.selection.base import SelectionContext

log = get_logger(__name__)


# ── ContentPlan / DraftPost serialization (round-trip via run.metadata) ──
def _plan_to_dict(plan: ContentPlan) -> dict[str, Any]:
    return {
        "rationale": plan.rationale,
        "source_summary": plan.source_summary,
        "blueprints": [_bp_to_dict(b) for b in plan.blueprints],
    }


def _plan_from_dict(d: dict[str, Any]) -> ContentPlan:
    return ContentPlan(
        rationale=d.get("rationale", ""),
        source_summary=d.get("source_summary", ""),
        blueprints=[_bp_from_dict(b) for b in (d.get("blueprints") or [])],
    )


def _bp_to_dict(bp: PostBlueprint) -> dict[str, Any]:
    return {
        "platform_name": bp.platform_name,
        "angle": bp.angle, "hook": bp.hook,
        "key_messages": list(bp.key_messages),
        "cta": bp.cta,
        "hashtags": [h.value for h in bp.hashtags],
        "media_prompt": bp.media_prompt,
        "media_kind": bp.media_kind.value if bp.media_kind else None,
        "notes": bp.notes,
    }


def _bp_from_dict(d: dict[str, Any]) -> PostBlueprint:
    return PostBlueprint(
        platform_name=d.get("platform_name", ""),
        angle=d.get("angle", ""), hook=d.get("hook", ""),
        key_messages=list(d.get("key_messages") or []),
        cta=d.get("cta"),
        hashtags=[Hashtag(value=v) for v in (d.get("hashtags") or [])],
        suggested_media=None,
        media_prompt=d.get("media_prompt"),
        media_kind=MediaKind(d["media_kind"]) if d.get("media_kind") else None,
        notes=d.get("notes"),
    )


def _draft_to_dict(d: DraftPost) -> dict[str, Any]:
    return {
        "platform_name": d.platform_name,
        "text": d.text,
        "hashtags": [h.value for h in d.hashtags],
        "media": [
            {"url": m.url, "kind": m.kind.value if m.kind else None,
             "alt_text": getattr(m, "alt_text", None)}
            for m in (d.media or [])
        ],
        "blueprint_ref": _bp_to_dict(d.blueprint_ref) if d.blueprint_ref else None,
    }


def _draft_from_dict(d: dict[str, Any]) -> DraftPost:
    return DraftPost(
        platform_name=d.get("platform_name", ""),
        text=d.get("text", ""),
        hashtags=[Hashtag(value=v) for v in (d.get("hashtags") or [])],
        media=[
            MediaAsset(
                url=m.get("url", ""),
                kind=MediaKind(m["kind"]) if m.get("kind") else MediaKind.IMAGE,
                alt_text=m.get("alt_text"),
            )
            for m in (d.get("media") or [])
            if m.get("url")
        ],
        blueprint_ref=_bp_from_dict(d["blueprint_ref"]) if d.get("blueprint_ref") else None,
    )


# ── Secret scrubbing ────────────────────────────────────────────────
# Strip anything key-shaped from a string before we persist it to a
# trace event or job error column. We had a Gemini incident (May 11
# 2026) where the user's API key landed in the Jobs UI because httpx
# included the request URL — `?key=AIzaSy…` — in its exception
# message. The Gemini provider has been changed to use a header, but
# this scrubber acts as defense-in-depth for any other provider that
# might do the same in the future.
_SECRET_PATTERNS = [
    # Google API keys (`AIza…`, 39 chars)
    re.compile(r"AIza[0-9A-Za-z\-_]{30,}"),
    # Bearer / api_key URL params
    re.compile(r"(?i)([?&](?:key|api[_-]?key|access[_-]?token)=)[^\s&'\"]+"),
    # Authorization headers
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+"),
    # OpenAI-style (`sk-…`, 20+ chars)
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    # Anthropic-style (`sk-ant-…`)
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    # Generic long base64-looking tokens (≥ 40 chars) in url params
    re.compile(r"(?i)([?&]token=)[^\s&'\"]{20,}"),
]


def _scrub_secrets(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        if pat.groups:
            out = pat.sub(r"\1<redacted>", out)
        else:
            out = pat.sub("<redacted>", out)
    return out


@dataclass(slots=True)
class PhaseResult:
    """Tiny return shape — handler reads ``next_phase`` to know what to
    enqueue. ``done=True`` means the run is over (success or terminal
    failure)."""
    next_phase: str | None = None
    done: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


class DurableWorkflowRunner:
    """Holds the same DI as WorkflowService but exposes phase methods."""

    def __init__(
        self, *,
        repo, run_repo, source_repo, platform_repo, post_repo,
        registry,
        review_repo=None,
        llm_credentials=None,
        llm_usage=None,
        source_items=None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.repo = repo
        self.run_repo = run_repo
        self.source_repo = source_repo
        self.platform_repo = platform_repo
        self.post_repo = post_repo
        self.review_repo = review_repo
        self.registry = registry
        self.llm_credentials = llm_credentials
        self.llm_usage = llm_usage
        self.source_items = source_items
        self.breaker = circuit_breaker
        # Pillar 4 — knowledge store wired in lazily; see _kb_context().
        self.knowledge_store = None
        # Per-run api_key cache — phases reuse the same decrypted key so
        # we don't hit the credentials store 5x per run.
        self._cached_api_key: dict[str, str | None] = {}

    # ── public API exposed to the job handlers ─────────────────────
    async def create_run_for_job(
        self, *, org_id: str, workflow_id: str,
        trigger_kind: str = "manual",
        trigger_payload: dict | None = None,
        directive: str | None = None,
        idempotency_key: str | None = None,
    ) -> WorkflowRun:
        run = WorkflowRun.create(
            org_id=OrgId(UUID(org_id) if isinstance(org_id, str) else org_id),
            workflow_id=WorkflowId(UUID(workflow_id) if isinstance(workflow_id, str) else workflow_id),
            directive=directive or "",
        )
        run.metadata["trigger_kind"] = trigger_kind
        run.metadata["trigger_payload"] = trigger_payload or {}
        run.metadata["idempotency_key"] = idempotency_key
        run.transition(RunStatus.QUEUED)
        await self.run_repo.add(run)
        return run

    async def run_phase(
        self, *, org_id: str | UUID, run_id: UUID | str, phase: str,
    ) -> dict[str, Any]:
        """Run a single phase by id. ``org_id`` is required because every
        repo's ``.get()`` is scoped to the org (no cross-tenant leakage).
        Handlers always have ``job.org_id`` available so this is cheap."""
        rid = UUID(run_id) if isinstance(run_id, str) else run_id
        oid_uuid = UUID(org_id) if isinstance(org_id, str) else org_id
        org_typed = OrgId(oid_uuid)

        run = await self.run_repo.get(org_typed, RunId(rid))
        if run is None:
            raise PermanentError(f"run {rid} not found")
        wf = await self.repo.get(org_typed, run.workflow_id)
        if wf is None:
            raise PermanentError(f"workflow {run.workflow_id} not found")

        method = getattr(self, f"_phase_{phase}", None)
        if method is None:
            raise PermanentError(f"unknown phase: {phase}")

        span_attrs = {
            "run.id": str(run.id), "org.id": str(run.org_id),
            "workflow.id": str(wf.id), "phase": phase,
        }
        try:
            with trace_span(f"run.phase.{phase}", **span_attrs):
                with M.run_duration.labels(phase=phase).time():
                    result = await method(run, wf)
        except CircuitOpen as exc:
            # Convert to a transient error — the worker will retry,
            # and by then the breaker may have closed.
            run.append(AgentTraceEvent(
                agent="runner", event="circuit_open",
                payload={"kind": exc.kind, "target": exc.target,
                         "retry_at": exc.retry_at.isoformat()},
            ))
            await self.run_repo.update(run)
            raise RuntimeError(f"circuit open: {exc}") from exc
        except PermanentError:
            run.transition(RunStatus.FAILED)
            run.error = "phase failed permanently"
            await self.run_repo.update(run)
            raise
        except Exception as exc:                                     # noqa: BLE001
            # Translate missing-LLM-credential errors into a clean
            # FAILED state with an actionable message. These are
            # permanent (no key won't appear via retry), so we also
            # raise PermanentError so the worker DLQs the job instead
            # of retrying 5x against the same broken config.
            from app.adapters.llm.base import MissingLLMCredentialError
            if isinstance(exc, MissingLLMCredentialError):
                run.transition(RunStatus.FAILED)
                run.error = (
                    f"LLM credentials missing for provider "
                    f"'{exc.provider}'. Add the key under "
                    f"Settings → LLM credentials and re-run."
                )
                run.append(AgentTraceEvent(
                    agent="runner", event="llm_credentials_missing",
                    payload={"provider": exc.provider,
                             "env_var": exc.env_var,
                             "phase": phase},
                ))
                await self.run_repo.update(run)
                raise PermanentError(str(exc)) from exc

            # Scrub anything that looks like an API key, bearer token,
            # or `?key=...` URL parameter before persisting the error to
            # the trace. We had a Gemini incident where the full URL —
            # including the API key — landed in the job error column.
            err_text = _scrub_secrets(str(exc))[:1000]
            run.append(AgentTraceEvent(
                agent="runner", event=f"phase_{phase}_failed",
                payload={"error": err_text},
            ))
            await self.run_repo.update(run)
            # Re-raise a clean copy so the queue's own error column also
            # gets the scrubbed text.
            raise RuntimeError(err_text) from None

        await self.run_repo.update(run)
        out: dict[str, Any] = {**result.extras}
        if result.done:
            out["done"] = True
        if result.next_phase:
            out["next"] = result.next_phase
        # Always surface the run's current revision counter — every
        # phase in a revision cycle (execute → critique → execute →
        # critique …) needs it in its idempotency key so each
        # revision's job is distinct from the prior one. Without this,
        # the second critique after a "revise" decision dedupes
        # against the first critique and the chain stops silently.
        revision = int(run.metadata.get("rerun_count", 0) or 0)
        if revision > 0 and "rerun_count" not in out:
            out["rerun_count"] = revision
        return out

    # ── phase: select ──────────────────────────────────────────────
    async def _phase_select(self, run: WorkflowRun, wf) -> PhaseResult:
        run.transition(RunStatus.SELECTING)
        # Resolve the workflow's source IDs into Source rows so we can
        # hand them to load_items. The repo's .get() is org-scoped.
        sources = []
        for sid in wf.source_ids:
            try:
                s = await self.source_repo.get(run.org_id, sid)
                if s:
                    sources.append(s)
            except Exception as exc:                                 # noqa: BLE001
                log.warning("durable_source_lookup_failed",
                            source_id=str(sid), error=str(exc))

        # Load items via the real loader signature. The loader already
        # annotates each SourceItem.metadata in-place with source_id +
        # source_plugin, so we don't need to do it ourselves.
        items = await load_items(sources, self.registry)

        if not items:
            run.transition(RunStatus.SUCCEEDED)
            run.error = "no source items"
            return PhaseResult(done=True, extras={"reason": "empty",
                                                   "count": 0})

        # Build the strategy. registry.get raises PluginNotRegisteredError
        # on miss — catch and fall back to the always-present freshness.
        strategy_name = wf.config.selection_strategy or "freshness"
        try:
            StrategyCls = self.registry.get(PluginKind.SELECTION, strategy_name).cls
        except Exception:                                            # noqa: BLE001
            StrategyCls = self.registry.get(PluginKind.SELECTION, "freshness").cls
        strategy = StrategyCls(wf.config.selection_config or {})

        # Consumed keys for de-dup. SourceItemsService.consumed_keys
        # requires the source_id list as its second positional arg.
        consumed: set[tuple[str, str]] = set()
        if self.source_items is not None and sources:
            try:
                consumed = await self.source_items.consumed_keys(
                    run.org_id, [s.id for s in sources],
                )
            except Exception as exc:                                 # noqa: BLE001
                log.warning("durable_consumed_keys_failed", error=str(exc))

        # Build LLM if the strategy needs it.
        llm = None
        if getattr(strategy, "needs_llm", False):
            try:
                api_key = await self._resolve_llm_api_key(
                    run.org_id, wf.config.llm_provider)
                orch = build_orchestrator(
                    wf, self.registry, api_key=api_key,
                    org_id=run.org_id, usage_service=self.llm_usage,
                    usage_context={
                        "workflow_id": str(wf.id),
                        "run_id": str(run.id), "trigger": "selection",
                    },
                )
                llm = orch.executor.llm
            except Exception as exc:                                 # noqa: BLE001
                log.warning("durable_select_llm_init_failed",
                            error=str(exc))

        ctx = SelectionContext(
            candidates=items, consumed_keys=consumed,
            workflow_config=wf.config,
            target_platforms=[str(p) for p in wf.platform_ids],
            directive=run.directive or None,
            org_id=run.org_id, llm=llm,
        )
        result = await strategy.select(ctx)

        run.append(AgentTraceEvent(
            agent="selector", event="strategy_complete",
            payload={
                "strategy": strategy_name,
                "candidates": result.candidates,
                "chosen": len(result.chosen),
                "skipped": len(result.skipped),
                "rationale": result.rationale,
            },
        ))

        if not result.chosen:
            run.transition(RunStatus.SUCCEEDED)
            run.error = "selection produced no items"
            return PhaseResult(done=True, extras={"count": 0,
                                                   "reason": "no-chosen"})

        # Persist chosen items into metadata for downstream phases.
        run.metadata["selected_items"] = [
            {
                "external_id": it.external_id,
                "title": it.title,
                "body": it.body,
                "source_id": str(it.metadata.get("source_id", "")),
                "metadata": it.metadata,
                "url": getattr(it, "url", None),
                "published_at": it.published_at.isoformat() if it.published_at else None,
            }
            for it in result.chosen
        ]
        run.metadata["selection_mode"] = result.mode.value

        # Atomic claim-on-select happens via mark_consumed in _phase_publish
        # once we have a real Post id to attribute consumption to. The
        # consumed_keys filter above + idempotency-key on the select job
        # together prevent the same run from re-selecting an item even if
        # its job retries.

        return PhaseResult(
            next_phase="plan",
            extras={"count": len(result.chosen), "mode": result.mode.value},
        )

    # ── phase: plan ────────────────────────────────────────────────
    async def _phase_plan(self, run: WorkflowRun, wf) -> PhaseResult:
        run.transition(RunStatus.PLANNING)
        orch = await self._build_orchestrator(run, wf, trigger="plan")
        state = await self._build_state(run, wf,
                                          target_platforms=await self._target_platform_kinds(wf))
        state = await orch.planner.run(state)
        if state.plan is None:
            run.transition(RunStatus.FAILED)
            run.error = "planner produced no plan"
            return PhaseResult(done=True, extras={"reason": "no_plan"})

        run.metadata["plan"] = _plan_to_dict(state.plan)
        run.append(AgentTraceEvent(
            agent="planner", event="plan_complete",
            payload={
                "blueprint_count": len(state.plan.blueprints),
                "platforms": [b.platform_name for b in state.plan.blueprints],
                "summary": (state.plan.source_summary or "")[:300],
            },
        ))

        # Tailor only worth running with >1 platform OR a compliance
        # profile that might filter platforms.
        multi = len(state.plan.blueprints) > 1
        has_compliance = bool(getattr(wf.config, "compliance_profile", None))
        return PhaseResult(
            next_phase="tailor" if (multi or has_compliance) else "execute",
            extras={
                "multi_platform": multi,
                "blueprint_count": len(state.plan.blueprints),
            },
        )

    # ── phase: tailor (Pillar 5) ───────────────────────────────────
    async def _phase_tailor(self, run: WorkflowRun, wf) -> PhaseResult:
        """Refine each PostBlueprint per platform's idiom + brand voice.
        Compliance-skipped platforms are *removed* from the plan so
        downstream phases never produce drafts for them."""
        plan_dict = run.metadata.get("plan")
        if not plan_dict:
            run.transition(RunStatus.FAILED)
            run.error = "tailor: missing plan from prior phase"
            return PhaseResult(done=True, extras={"reason": "no_plan"})

        plan = _plan_from_dict(plan_dict)
        orch = await self._build_orchestrator(run, wf, trigger="tailor")

        # Lazy services.
        kb = None
        hashtag_svc = None
        try:
            from app.api.deps import get_knowledge_store
            kb = get_knowledge_store()
        except Exception:                                            # noqa: BLE001
            pass
        try:
            from app.api.deps import get_hashtag_intelligence_service
            hashtag_svc = get_hashtag_intelligence_service()
        except Exception:                                            # noqa: BLE001
            pass

        from app.agents.tailor_agent import TailorAgent
        agent = TailorAgent(
            llm=orch.executor.llm,
            hashtag_service=hashtag_svc,
            knowledge_store=kb,
        )
        refined, telemetry = await agent.refine(
            plan=plan, workflow_config=wf.config,
            org_id=str(run.org_id),
            directive=run.directive or None,
        )

        if not refined.blueprints:
            run.transition(RunStatus.SUCCEEDED)
            run.error = "tailor removed every platform (compliance)"
            return PhaseResult(
                done=True,
                extras={"reason": "all_compliance_skipped",
                         **telemetry},
            )

        run.metadata["plan"] = _plan_to_dict(refined)
        run.metadata["tailor_telemetry"] = telemetry
        run.append(AgentTraceEvent(
            agent="tailor", event="plan_refined", payload=telemetry,
        ))
        return PhaseResult(next_phase="execute",
                           extras=dict(telemetry))

    # ── phase: execute ─────────────────────────────────────────────
    async def _phase_execute(self, run: WorkflowRun, wf) -> PhaseResult:
        run.transition(RunStatus.EXECUTING)
        plan_dict = run.metadata.get("plan")
        if not plan_dict:
            run.transition(RunStatus.FAILED)
            run.error = "execute: missing plan from prior phase"
            return PhaseResult(done=True, extras={"reason": "no_plan"})

        plan = _plan_from_dict(plan_dict)
        orch = await self._build_orchestrator(run, wf, trigger="execute")

        # Pillar 4 — RAG retrieval. Build a query from directive +
        # source_summary, ask the KB for top-K chunks, splice the
        # formatted block into ``state.voice_block`` so the existing
        # Executor prompt-builder picks it up automatically.
        kb_query = " ".join(filter(None, [
            run.directive or "",
            plan.source_summary or "",
            *[bp.angle for bp in plan.blueprints[:3]],
        ])).strip()
        voice_block = ""
        if kb_query:
            voice_block = await self._kb_context(
                org_id=run.org_id, query=kb_query,
                llm=orch.executor.llm,
            )
            if voice_block:
                run.metadata["kb_context_chars"] = len(voice_block)
                run.append(AgentTraceEvent(
                    agent="kb", event="rag_retrieved",
                    payload={"chars": len(voice_block)},
                ))

        state = await self._build_state(
            run, wf, target_platforms=[bp.platform_name for bp in plan.blueprints],
            plan=plan, voice_block=voice_block,
        )
        state = await orch.executor.run(state)

        run.metadata["drafts"] = [_draft_to_dict(d) for d in state.drafts]
        run.append(AgentTraceEvent(
            agent="executor", event="drafts_complete",
            payload={
                "count": len(state.drafts),
                "platforms": [d.platform_name for d in state.drafts],
            },
        ))
        return PhaseResult(next_phase="critique",
                           extras={"count": len(state.drafts)})

    # ── phase: critique ────────────────────────────────────────────
    async def _phase_critique(self, run: WorkflowRun, wf) -> PhaseResult:
        """Run Evaluator + Critique on the assembled drafts. The
        existing agents work on the same AgentState; we hand them
        a state with .plan and .drafts populated."""
        run.transition(RunStatus.CRITIQUING)

        plan_dict = run.metadata.get("plan")
        drafts_dicts = run.metadata.get("drafts") or []
        if not plan_dict or not drafts_dicts:
            run.transition(RunStatus.FAILED)
            run.error = "critique: missing plan or drafts"
            return PhaseResult(done=True, extras={"reason": "no_input"})

        plan = _plan_from_dict(plan_dict)
        drafts = [_draft_from_dict(d) for d in drafts_dicts]
        orch = await self._build_orchestrator(run, wf, trigger="critique")

        state = await self._build_state(
            run, wf,
            target_platforms=[d.platform_name for d in drafts],
            plan=plan, drafts=drafts,
            critique_notes=list(run.metadata.get("critique_notes") or []),
            revision_count=int(run.metadata.get("rerun_count", 0)),
        )
        state = await orch.evaluator.run(state)
        state = await orch.critique.run(state)

        # Persist evaluator output for the trace + the dashboard.
        # EvaluationReport fields per app/domain/value_objects/content.py
        # — there is no per-evaluation platform_name field; the index
        # aligns with state.drafts so callers infer platform that way.
        run.metadata["evaluations"] = [
            {"overall":             float(e.overall),
             "clarity":             float(e.clarity),
             "brand_voice":         float(e.brand_voice),
             "compliance":          float(e.compliance),
             "platform_fit":        float(e.platform_fit),
             "predicted_engagement": float(e.predicted_engagement),
             "suggestions": list(e.suggestions),
             "flags":       list(e.flags)}
            for e in state.evaluations
        ]
        run.metadata["critique_notes"] = list(state.critique_notes)
        decision = state.decision

        run.append(AgentTraceEvent(
            agent="critique", event="decision",
            payload={
                "decision": decision.value if decision else "none",
                "evaluations": run.metadata["evaluations"],
                "notes_count": len(state.critique_notes),
            },
        ))

        if decision is AgentDecision.ESCALATE:
            run.transition(RunStatus.AWAITING_REVIEW)
            review_ids = await self._materialize_drafts_as_review(
                run, wf, drafts, state.evaluations,
                reason="critique_escalated",
            )
            return PhaseResult(extras={"requires_review": True,
                                       "review_post_ids": review_ids})

        if decision is AgentDecision.REVISE:
            count = int(run.metadata.get("rerun_count", 0)) + 1
            run.metadata["rerun_count"] = count
            max_revisions = int(getattr(wf.config, "max_revisions", 3) or 3)
            if count > max_revisions:
                run.transition(RunStatus.AWAITING_REVIEW)
                run.error = f"exhausted {max_revisions} revisions"
                # Don't lose the work — surface the best draft for a
                # human to approve / edit. The run lands in AWAITING_REVIEW
                # rather than FAILED so the Reviews + Posts pages light up.
                review_ids = await self._materialize_drafts_as_review(
                    run, wf, drafts, state.evaluations,
                    reason="rerun_exhausted",
                )
                return PhaseResult(done=True,
                                    extras={"reason": "rerun_exhausted",
                                            "review_post_ids": review_ids})
            # Re-execute with the critique notes — Plan stays the same,
            # only Executor reruns.
            return PhaseResult(next_phase="execute",
                                extras={"rerun": True,
                                        "rerun_count": count})

        if decision is AgentDecision.ABORT:
            run.transition(RunStatus.FAILED)
            run.error = "critique aborted run"
            return PhaseResult(done=True, extras={"reason": "aborted"})

        # APPROVE (or None — accepts).
        # If the workflow requires human approval, route to AWAITING_REVIEW
        # instead of auto-publishing. Otherwise the require_human_approval
        # flag would be silently ignored when critique returns APPROVE.
        # ESCALATE already does this above; APPROVE must respect it too.
        if getattr(wf.config, "require_human_approval", False):
            run.transition(RunStatus.AWAITING_REVIEW)
            run.append(AgentTraceEvent(
                agent="critique", event="awaiting_human_approval",
                payload={"reason": "workflow.config.require_human_approval"},
            ))
            review_ids = await self._materialize_drafts_as_review(
                run, wf, drafts, state.evaluations,
                reason="require_human_approval",
            )
            return PhaseResult(extras={"requires_review": True,
                                       "auto_approved": True,
                                       "review_post_ids": review_ids})
        return PhaseResult(next_phase="publish")

    # ── phase: publish ─────────────────────────────────────────────
    async def _phase_publish(self, run: WorkflowRun, wf) -> PhaseResult:
        run.transition(RunStatus.PUBLISHING)
        drafts_dicts = run.metadata.get("drafts") or []
        already_published = set(run.metadata.get("post_ids") or [])
        drafts = [_draft_from_dict(d) for d in drafts_dicts]
        platforms = await self._load_platforms(wf, run.org_id)
        post_ids: list[str] = list(already_published)

        for draft in drafts:
            # Skip drafts whose post is already in run.metadata['post_ids']
            # (publish is per-platform; partial retry must not double-post).
            if draft.platform_name in run.metadata.get("published_platforms", []):
                continue
            target = self._match_platform_for_draft(draft, platforms)
            if target is None:
                run.append(AgentTraceEvent(
                    agent="publisher", event="no_target_platform",
                    payload={"draft_platform": draft.platform_name},
                ))
                continue
            try:
                post = await self._publish_one(run, wf, draft, target)
                post_ids.append(str(post.id))
                run.metadata.setdefault("published_platforms", []).append(
                    draft.platform_name)
            except CircuitOpen as exc:
                # Re-raise → worker retries. Already-published drafts
                # remain marked so the retry only handles the rest.
                run.append(AgentTraceEvent(
                    agent="publisher", event="circuit_open_skip",
                    payload={"target": exc.target},
                ))
                raise

        run.metadata["post_ids"] = post_ids
        if post_ids:
            run.transition(RunStatus.SUCCEEDED)
            run.append(AgentTraceEvent(
                agent="publisher", event="run_complete",
                payload={"post_count": len(post_ids)},
            ))
        else:
            run.transition(RunStatus.FAILED)
            run.error = "no posts produced"
        return PhaseResult(done=True,
                           extras={"post_ids": post_ids})

    # ── helpers ────────────────────────────────────────────────────
    async def _kb_context(self, *, org_id, query, llm) -> str:
        """Resolve the knowledge store lazily (avoids importing deps at
        module load time) and call the retriever. Returns the
        formatted context block or empty string on failure."""
        if self.knowledge_store is None:
            try:
                from app.api.deps import get_knowledge_store
                self.knowledge_store = get_knowledge_store()
            except Exception:                                        # noqa: BLE001
                return ""
        try:
            from app.services.knowledge.retriever import retrieve_for_prompt
            return await retrieve_for_prompt(
                store=self.knowledge_store, llm=llm, query=query,
                org_id=str(org_id), top_k=5,
            )
        except Exception as exc:                                     # noqa: BLE001
            log.info("kb_context_skipped", error=str(exc))
            return ""

    async def _resolve_llm_api_key(self, org_id, provider):
        if self.llm_credentials is None:
            return None
        try:
            return await self.llm_credentials.decrypt_key(org_id, provider)
        except Exception:                                            # noqa: BLE001
            return None

    def _items_from_metadata(self, run: WorkflowRun):
        from app.domain.entities.source import SourceItem
        out = []
        for d in run.metadata.get("selected_items") or []:
            published = None
            if d.get("published_at"):
                try:
                    published = datetime.fromisoformat(d["published_at"])
                except Exception:                                    # noqa: BLE001
                    published = None
            out.append(SourceItem(
                external_id=d["external_id"], title=d["title"],
                body=d.get("body") or "",
                metadata=d.get("metadata") or {},
                published_at=published,
            ))
        return out

    async def _load_platforms(self, wf, org_id):
        """Returns the workflow's bound Platform rows. The repo's
        .get() is org-scoped so we pass org_id."""
        plats = []
        for pid in wf.platform_ids:
            try:
                p = await self.platform_repo.get(org_id, pid)
                if p:
                    plats.append(p)
            except Exception as exc:                                 # noqa: BLE001
                log.warning("durable_platform_lookup_failed",
                            platform_id=str(pid), error=str(exc))
        return plats

    async def _target_platform_kinds(self, wf) -> list[str]:
        """Resolve plugin-name strings for the workflow's platforms.
        ``Platform.plugin_name`` (NOT ``.kind``) is the registry key
        — the Planner uses these to fan out per-platform Blueprints."""
        names: list[str] = []
        for pid in wf.platform_ids:
            try:
                p = await self.platform_repo.get(wf.org_id, pid)
                if p:
                    names.append(p.plugin_name)
            except Exception as exc:                                 # noqa: BLE001
                log.warning("durable_platform_lookup_failed",
                            platform_id=str(pid), error=str(exc))
        return names or [str(p) for p in wf.platform_ids]

    async def _materialize_drafts_as_review(
        self, run: WorkflowRun, wf,
        drafts: list[DraftPost],
        evaluations: list | None = None,
        *, reason: str,
    ) -> list[str]:
        """Persist each draft as a Post(status=REVIEW) so it shows up in
        the Posts page and Reviews queue when the run stops short of
        autopublish (escalated by critique, blocked by
        require_human_approval, or revisions exhausted).

        Without this, drafts only live in ``run.metadata['drafts']`` and
        the user sees an empty Posts page after green-looking job runs.

        Returns the list of newly created post_ids (string form). The
        IDs are also appended to ``run.metadata['review_post_ids']`` so
        a partial-retry of this phase doesn't double-create.
        """
        if not drafts:
            return []
        already = set(run.metadata.get("review_post_ids") or [])
        platforms = await self._load_platforms(wf, run.org_id)
        new_ids: list[str] = []

        for idx, draft in enumerate(drafts):
            # Idempotency: if a post already exists for this
            # (run_id, platform_name) pair on retry, skip.
            tag = f"{draft.platform_name}:{idx}"
            if tag in run.metadata.get("review_post_tags", []):
                continue

            target = self._match_platform_for_draft(draft, platforms)
            if target is None:
                run.append(AgentTraceEvent(
                    agent="critique", event="review_post_skipped_no_platform",
                    payload={"draft_platform": draft.platform_name},
                ))
                continue

            # Attach the matching evaluation by index — evaluator output
            # is parallel to state.drafts (same order).
            eval_obj = None
            if evaluations and idx < len(evaluations):
                eval_obj = evaluations[idx]

            post = Post.from_draft(
                org_id=run.org_id, workflow_id=wf.id, run_id=run.id,
                platform_id=target.id, draft=draft, evaluation=eval_obj,
            )
            post.status = PostStatus.REVIEW
            # Stash critique notes + reason on the post so the Reviews
            # UI can show "why this needs human eyes".
            post.error = None
            try:
                await self.post_repo.add(post)
            except Exception as exc:                                  # noqa: BLE001
                log.warning("review_post_persist_failed",
                            run_id=str(run.id),
                            platform=draft.platform_name,
                            error=str(exc))
                continue

            new_ids.append(str(post.id))
            run.metadata.setdefault("review_post_tags", []).append(tag)

        if new_ids:
            run.metadata["review_post_ids"] = sorted(already.union(new_ids))
            run.append(AgentTraceEvent(
                agent="critique", event="drafts_persisted_for_review",
                payload={"reason": reason,
                         "count": len(new_ids),
                         "post_ids": new_ids},
            ))
        return new_ids

    def _match_platform_for_draft(self, draft: DraftPost, platforms):
        """Pick the Platform row matching this draft's target. Match
        on plugin_name since that's what the Planner's PostBlueprint
        sets as ``platform_name``."""
        if not platforms:
            return None
        for p in platforms:
            if p.plugin_name == draft.platform_name:
                return p
        # Fall back to the first platform when the plugin_name doesn't
        # line up — better than dropping the draft entirely.
        return platforms[0]

    async def _build_orchestrator(self, run: WorkflowRun, wf, *, trigger: str):
        """Async because we may need to decrypt the api_key first.
        Reuses a per-run cache so multi-phase runs don't re-decrypt."""
        if str(run.id) not in self._cached_api_key:
            self._cached_api_key[str(run.id)] = await self._resolve_llm_api_key(
                run.org_id, wf.config.llm_provider,
            )
        api_key = self._cached_api_key[str(run.id)]
        return build_orchestrator(
            wf, self.registry, api_key=api_key,
            org_id=run.org_id, usage_service=self.llm_usage,
            usage_context={
                "workflow_id": str(wf.id),
                "run_id": str(run.id), "trigger": trigger,
            },
        )

    async def _build_state(
        self, run: WorkflowRun, wf, *,
        target_platforms: list[str],
        plan: ContentPlan | None = None,
        drafts: list[DraftPost] | None = None,
        voice_block: str = "",
        critique_notes: list[str] | None = None,
        revision_count: int = 0,
    ) -> AgentState:
        items = self._items_from_metadata(run)
        return AgentState(
            workflow_config=wf.config,
            target_platforms=target_platforms,
            source_items=items,
            plan=plan,
            drafts=list(drafts) if drafts else [],
            critique_notes=list(critique_notes or []),
            revision_count=int(revision_count),
            directive=run.directive or "",
            voice_block=voice_block or "",
        )

    async def _publish_one(self, run, wf, draft: DraftPost, target_platform):
        breaker = self.breaker
        org_id = str(run.org_id)
        kind = "platform"
        # Platform.plugin_name is the registry key — NOT a .kind attr.
        plat_key = target_platform.plugin_name

        async def _do():
            try:
                adapter_cls = self.registry.get(PluginKind.PLATFORM, plat_key).cls
            except Exception:                                        # noqa: BLE001
                raise PermanentError(f"no adapter for platform {plat_key}")
            # Pass OAuth credentials + per-account config so the adapter
            # can authenticate against the platform API.
            adapter = adapter_cls(
                credentials=getattr(target_platform, "credentials", None),
                config=getattr(target_platform, "config", None) or {},
            )
            # PostPayload uses ``text`` / ``hashtags`` / ``media`` — not
            # ``content`` / ``title`` / ``media_urls`` (that was an
            # earlier guess; the real shape lives in adapters/platforms/
            # base.py). The hashtag list rides as a structured field so
            # adapters that have platform-specific tagging conventions
            # (Instagram caption block, X inline) can format correctly.
            payload = PostPayload(
                text=draft.text,
                hashtags=list(draft.hashtags),
                media=list(draft.media),
            )
            # SocialPlatform.publish(payload) — single positional arg.
            return await adapter.publish(payload)

        with trace_span("publish",
                        **{"org.id": org_id,
                           "platform": plat_key,
                           "run.id": str(run.id)}):
            with M.publish_duration.labels(platform=plat_key).time():
                try:
                    if breaker is not None:
                        result = await breaker.guard(org_id, kind, plat_key, _do)
                    else:
                        result = await _do()
                    M.publish_total.labels(
                        platform=plat_key, status="ok").inc()
                    M.circuit_state.labels(
                        kind=kind, target=plat_key).set(0)
                except CircuitOpen:
                    M.publish_total.labels(
                        platform=plat_key, status="circuit_open").inc()
                    M.circuit_state.labels(
                        kind=kind, target=plat_key).set(1)
                    raise
                except Exception:
                    M.publish_total.labels(
                        platform=plat_key, status="error").inc()
                    raise

        # Post.from_draft is the canonical constructor — copies
        # text/hashtags/media off the draft and stamps a new PostId.
        # (There is no ``Post.create`` despite what an older version
        # of this file assumed.)
        post = Post.from_draft(
            org_id=run.org_id, workflow_id=wf.id, run_id=run.id,
            platform_id=target_platform.id, draft=draft,
        )
        # mark_published handles status + external_post_id +
        # published_at in one go.
        post.mark_published(
            external_id=getattr(result, "external_post_id", "") or "",
        )
        await self.post_repo.add(post)
        return post


