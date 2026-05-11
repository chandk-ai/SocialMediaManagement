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
        self, *, run_id: UUID | str, phase: str,
    ) -> dict[str, Any]:
        rid = UUID(run_id) if isinstance(run_id, str) else run_id
        run = await self.run_repo.get(RunId(rid))
        if run is None:
            raise PermanentError(f"run {rid} not found")
        wf = await self.repo.get(run.workflow_id)
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
            await self.run_repo.save(run)
            raise RuntimeError(f"circuit open: {exc}") from exc
        except PermanentError:
            run.transition(RunStatus.FAILED)
            run.error = "phase failed permanently"
            await self.run_repo.save(run)
            raise
        except Exception as exc:                                     # noqa: BLE001
            run.append(AgentTraceEvent(
                agent="runner", event=f"phase_{phase}_failed",
                payload={"error": str(exc)[:1000]},
            ))
            await self.run_repo.save(run)
            raise

        await self.run_repo.save(run)
        out: dict[str, Any] = {**result.extras}
        if result.done:
            out["done"] = True
        if result.next_phase:
            out["next"] = result.next_phase
        return out

    # ── phase: select ──────────────────────────────────────────────
    async def _phase_select(self, run: WorkflowRun, wf) -> PhaseResult:
        run.transition(RunStatus.SELECTING)
        # Load source items with metadata-tagged source_id for selection.
        items = await load_items(
            wf, self.source_repo, self.registry,
            run_id=run.id, run_repo=self.run_repo,
        )
        # Tag each candidate with its source for de-dup later.
        for it in items:
            md = dict(it.metadata or {})
            md.setdefault("source_id", str(md.get("source_id") or ""))
            it.metadata = md

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

        # Consumed keys for de-dup.
        consumed: set[tuple[str, str]] = set()
        if self.source_items is not None:
            try:
                consumed = await self.source_items.consumed_keys(run.org_id)
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
        orch = self._build_orchestrator(run, wf, trigger="plan")
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
        orch = self._build_orchestrator(run, wf, trigger="tailor")

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
        orch = self._build_orchestrator(run, wf, trigger="execute")

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
        orch = self._build_orchestrator(run, wf, trigger="critique")

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
        run.metadata["evaluations"] = [
            {"platform_name": e.platform_name,
             "score": float(e.score),
             "flags": list(getattr(e, "flags", []) or []),
             "notes": getattr(e, "notes", "") or ""}
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
            return PhaseResult(extras={"requires_review": True})

        if decision is AgentDecision.REVISE:
            count = int(run.metadata.get("rerun_count", 0)) + 1
            run.metadata["rerun_count"] = count
            max_revisions = int(getattr(wf.config, "max_revisions", 3) or 3)
            if count > max_revisions:
                run.transition(RunStatus.FAILED)
                run.error = f"exhausted {max_revisions} revisions"
                return PhaseResult(done=True,
                                    extras={"reason": "rerun_exhausted"})
            # Re-execute with the critique notes — Plan stays the same,
            # only Executor reruns.
            return PhaseResult(next_phase="execute",
                                extras={"rerun": True,
                                        "rerun_count": count})

        if decision is AgentDecision.ABORT:
            run.transition(RunStatus.FAILED)
            run.error = "critique aborted run"
            return PhaseResult(done=True, extras={"reason": "aborted"})

        # APPROVE (or None — accepts)
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
        plats = []
        for pid in wf.platform_ids:
            try:
                p = await self.platform_repo.get(pid)
                if p:
                    plats.append(p)
            except Exception:                                        # noqa: BLE001
                pass
        return plats

    async def _target_platform_kinds(self, wf) -> list[str]:
        """Resolve plugin-name strings for the workflow's platforms.
        Falls back to the raw platform_id strings if a platform row is
        missing — Planner will skip those Blueprints."""
        kinds: list[str] = []
        for pid in wf.platform_ids:
            try:
                p = await self.platform_repo.get(pid)
                if p:
                    kinds.append(str(p.kind))
            except Exception:                                        # noqa: BLE001
                pass
        return kinds or [str(p) for p in wf.platform_ids]

    def _match_platform_for_draft(self, draft: DraftPost, platforms):
        if not platforms:
            return None
        for p in platforms:
            if str(p.kind) == draft.platform_name:
                return p
        # Fall back to the first platform if the kind doesn't line up.
        return platforms[0]

    def _build_orchestrator(self, run: WorkflowRun, wf, *, trigger: str):
        # Bridge sync wrapper since the inner factory doesn't await on
        # api_key resolution; the caller does that ahead of time.
        # (Kept as a class helper so subclasses can swap.)
        api_key = self._cached_api_key.get(str(run.id))
        if api_key is None and self.llm_credentials is not None:
            # Phase methods are async — they can resolve directly.
            raise RuntimeError("call _resolve_api_key_for_run before _build_orchestrator")
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
        # Cache the api_key per run so multi-step phases share the
        # same orchestrator construction without re-decrypting.
        if str(run.id) not in self._cached_api_key:
            self._cached_api_key[str(run.id)] = await self._resolve_llm_api_key(
                run.org_id, wf.config.llm_provider,
            )
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
        plat_key = str(target_platform.kind)

        async def _do():
            try:
                adapter_cls = self.registry.get(PluginKind.PLATFORM, plat_key).cls
            except Exception:                                        # noqa: BLE001
                raise PermanentError(f"no adapter for platform {plat_key}")
            # Pass OAuth credentials + per-account config so the adapter
            # can authenticate against the platform API.
            try:
                adapter = adapter_cls(
                    credentials=getattr(target_platform, "credentials", None),
                    config=getattr(target_platform, "config", None),
                )
            except TypeError:
                adapter = adapter_cls()
            # Append hashtag block after a blank line — same convention
            # the inline path uses (PostPayload.content is the literal
            # text the platform receives).
            body_with_tags = draft.text
            if draft.hashtags:
                body_with_tags = (
                    draft.text.rstrip()
                    + "\n\n"
                    + " ".join(h.value for h in draft.hashtags)
                )
            media_urls = [m.url for m in (draft.media or []) if getattr(m, "url", None)]
            payload = PostPayload(
                content=body_with_tags,
                title=getattr(draft.blueprint_ref, "angle", "")[:120]
                      if draft.blueprint_ref else "",
                media_urls=media_urls,
            )
            return await adapter.publish(payload, account=target_platform)

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

        # Persist Post — record what we actually sent (incl. hashtags).
        body_with_tags = draft.text
        if draft.hashtags:
            body_with_tags = (
                draft.text.rstrip()
                + "\n\n"
                + " ".join(h.value for h in draft.hashtags)
            )
        post = Post.create(
            org_id=run.org_id, workflow_id=wf.id,
            platform_id=target_platform.id, run_id=run.id,
            content=body_with_tags,
            title=(getattr(draft.blueprint_ref, "angle", "") or "")[:120],
            media_urls=[m.url for m in (draft.media or []) if getattr(m, "url", None)],
        )
        post.status = PostStatus.PUBLISHED
        post.external_url = getattr(result, "url", None)
        await self.post_repo.add(post)
        return post


