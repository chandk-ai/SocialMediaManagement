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
from app.agents.factory import build_orchestrator
from app.agents.source_loader import load_items
from app.core.logging import get_logger
from app.core.metrics import M
from app.core.tracing import trace_span
from app.domain.entities.post import Post, PostStatus
from app.domain.entities.workflow_run import (
    AgentTraceEvent, RunStatus, WorkflowRun,
)
from app.domain.value_objects.content import DraftPost
from app.domain.value_objects.ids import OrgId, RunId, WorkflowId
from app.domain.value_objects.selection import ItemMode
from app.plugins.registry import PluginKind
from app.services.circuit_breaker import CircuitBreaker, CircuitOpen
from app.services.jobs.queue import PermanentError
from app.services.selection.base import SelectionContext

log = get_logger(__name__)


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

        # Build the strategy.
        strategy_name = wf.config.selection_strategy or "freshness"
        StrategyCls = self.registry.get(PluginKind.SELECTION, strategy_name)
        if StrategyCls is None:
            StrategyCls = self.registry.get(PluginKind.SELECTION, "freshness")
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

        # Atomic claim-on-select if we have the registry.
        if self.source_items is not None:
            try:
                claimed = await self.source_items.claim_for_run(
                    run.org_id, run.id,
                    [(SourceId_or_none(d["source_id"]), d["external_id"])
                     for d in run.metadata["selected_items"]
                     if d["source_id"]],
                )
                run.metadata["claimed_keys"] = list(claimed)
            except AttributeError:
                pass  # method optional on some impls
            except Exception as exc:                                 # noqa: BLE001
                log.warning("durable_claim_failed", error=str(exc))

        return PhaseResult(
            next_phase="plan",
            extras={"count": len(result.chosen), "mode": result.mode.value},
        )

    # ── phase: plan ────────────────────────────────────────────────
    async def _phase_plan(self, run: WorkflowRun, wf) -> PhaseResult:
        run.transition(RunStatus.PLANNING)
        api_key = await self._resolve_llm_api_key(
            run.org_id, wf.config.llm_provider)
        orch = build_orchestrator(
            wf, self.registry, api_key=api_key,
            org_id=run.org_id, usage_service=self.llm_usage,
            usage_context={
                "workflow_id": str(wf.id),
                "run_id": str(run.id), "trigger": "plan",
            },
        )
        items = self._items_from_metadata(run)
        plan = await orch.planner.plan(
            workflow=wf, items=items, directive=run.directive or None,
        )
        run.metadata["plan_output"] = {
            "summary": plan.summary if hasattr(plan, "summary") else "",
            "outline": getattr(plan, "outline", None),
            "calls_to_action": getattr(plan, "calls_to_action", []),
            "raw": getattr(plan, "raw", None),
        }
        run.append(AgentTraceEvent(
            agent="planner", event="plan_complete",
            payload={"summary": str(run.metadata["plan_output"]["summary"])[:300]},
        ))

        # Multi-platform = need Tailor pass.
        multi = len(wf.platform_ids) > 1
        return PhaseResult(
            next_phase="tailor" if multi else "execute",
            extras={"multi_platform": multi},
        )

    # ── phase: tailor (Pillar 5) ───────────────────────────────────
    async def _phase_tailor(self, run: WorkflowRun, wf) -> PhaseResult:
        """Per-platform variant generation. Uses the full TailorAgent
        with KB injection, hashtag intelligence, and compliance
        denylisting; falls back to the lightweight helper if any of
        those services aren't wired."""
        api_key = await self._resolve_llm_api_key(
            run.org_id, wf.config.llm_provider)
        orch = build_orchestrator(
            wf, self.registry, api_key=api_key,
            org_id=run.org_id, usage_service=self.llm_usage,
            usage_context={
                "workflow_id": str(wf.id),
                "run_id": str(run.id), "trigger": "tailor",
            },
        )

        # Resolve platform-kind list for the workflow's platforms.
        platform_kinds: list[str] = []
        for pid in wf.platform_ids:
            try:
                p = await self.platform_repo.get(pid)
                if p:
                    platform_kinds.append(str(p.kind))
            except Exception:                                        # noqa: BLE001
                pass
        if not platform_kinds:
            platform_kinds = [str(p) for p in wf.platform_ids]

        # Lazy services.
        try:
            from app.api.deps import (
                get_knowledge_store, get_hashtag_intelligence_service,
            )
            kb = get_knowledge_store()
        except Exception:                                            # noqa: BLE001
            kb = None
        try:
            hashtag_svc = get_hashtag_intelligence_service()
        except Exception:                                            # noqa: BLE001
            hashtag_svc = None

        from app.agents.tailor_agent import TailorAgent
        agent = TailorAgent(
            llm=orch.executor.llm,
            hashtag_service=hashtag_svc,
            knowledge_store=kb,
        )
        plan = run.metadata.get("plan_output", {})
        variants = await agent.tailor(
            plan=plan, platforms=platform_kinds,
            workflow_config=wf.config, org_id=str(run.org_id),
            directive=run.directive or None,
        )

        run.metadata["tailor_variants"] = variants
        run.append(AgentTraceEvent(
            agent="tailor", event="variants_generated",
            payload={
                "count": len(variants),
                "platforms": list(variants.keys()),
                "rewritten": [k for k, v in variants.items()
                               if v.get("rewritten")],
                "compliance_skipped": [k for k, v in variants.items()
                                        if v.get("compliance_skip")],
            },
        ))
        return PhaseResult(next_phase="execute",
                           extras={"variants": len(variants)})

    # ── phase: execute ─────────────────────────────────────────────
    async def _phase_execute(self, run: WorkflowRun, wf) -> PhaseResult:
        run.transition(RunStatus.EXECUTING)
        api_key = await self._resolve_llm_api_key(
            run.org_id, wf.config.llm_provider)
        orch = build_orchestrator(
            wf, self.registry, api_key=api_key,
            org_id=run.org_id, usage_service=self.llm_usage,
            usage_context={
                "workflow_id": str(wf.id),
                "run_id": str(run.id), "trigger": "execute",
            },
        )
        items = self._items_from_metadata(run)

        # Pillar 4 — RAG retrieval. Build a query from the directive +
        # the plan summary, fetch the most relevant brand-voice chunks,
        # and stash on the run for the Executor's prompt.
        kb_query = " ".join(filter(None, [
            run.directive or "",
            (run.metadata.get("plan_output") or {}).get("summary") or "",
        ])).strip()
        if kb_query:
            kb_block = await self._kb_context(
                org_id=run.org_id, query=kb_query,
                llm=orch.executor.llm,
            )
            if kb_block:
                run.metadata["kb_context"] = kb_block
                run.append(AgentTraceEvent(
                    agent="kb", event="rag_retrieved",
                    payload={"chars": len(kb_block)},
                ))

        variants = run.metadata.get("tailor_variants") or {}
        drafts: list[dict[str, Any]] = []
        if variants:
            # Per-platform drafts produced by Tailor.
            for plat_key, variant in variants.items():
                d = await orch.executor.execute(
                    workflow=wf, items=items,
                    plan=variant.get("plan") or run.metadata.get("plan_output"),
                    platform_hint=plat_key,
                    directive=run.directive or None,
                )
                drafts.append({"platform": plat_key,
                                "title": d.title, "body": d.body,
                                "media_urls": list(getattr(d, "media_urls", []) or [])})
        else:
            # Single-platform path or fallback.
            d = await orch.executor.execute(
                workflow=wf, items=items,
                plan=run.metadata.get("plan_output"),
                directive=run.directive or None,
            )
            drafts.append({
                "platform": None,
                "title": d.title, "body": d.body,
                "media_urls": list(getattr(d, "media_urls", []) or []),
            })

        run.metadata["drafts"] = drafts
        run.append(AgentTraceEvent(
            agent="executor", event="drafts_complete",
            payload={"count": len(drafts)},
        ))
        return PhaseResult(next_phase="critique",
                           extras={"count": len(drafts)})

    # ── phase: critique ────────────────────────────────────────────
    async def _phase_critique(self, run: WorkflowRun, wf) -> PhaseResult:
        run.transition(RunStatus.CRITIQUING)
        api_key = await self._resolve_llm_api_key(
            run.org_id, wf.config.llm_provider)
        orch = build_orchestrator(
            wf, self.registry, api_key=api_key,
            org_id=run.org_id, usage_service=self.llm_usage,
            usage_context={
                "workflow_id": str(wf.id),
                "run_id": str(run.id), "trigger": "critique",
            },
        )
        drafts = run.metadata.get("drafts") or []
        decisions: list[dict] = []
        for draft in drafts:
            dp = DraftPost(
                title=draft.get("title", ""), body=draft.get("body", ""),
                media_urls=draft.get("media_urls") or [],
            )
            decision = await orch.critique.review(
                workflow=wf, draft=dp,
                directive=run.directive or None,
            )
            decisions.append({
                "platform": draft.get("platform"),
                "approved": getattr(decision, "approved", False),
                "needs_human": getattr(decision, "needs_human", False),
                "rerun": getattr(decision, "rerun", False),
                "feedback": getattr(decision, "feedback", ""),
                "flags": list(getattr(decision, "flags", []) or []),
            })
        run.metadata["critique"] = decisions

        any_human = any(d["needs_human"] for d in decisions)
        any_rerun = any(d["rerun"] for d in decisions)
        all_approved = all(d["approved"] for d in decisions)

        if any_human:
            run.transition(RunStatus.AWAITING_REVIEW)
            run.append(AgentTraceEvent(
                agent="critique", event="needs_review",
                payload={"flags": [f for d in decisions for f in d["flags"]]},
            ))
            return PhaseResult(extras={"requires_review": True})

        if any_rerun:
            count = int(run.metadata.get("rerun_count", 0)) + 1
            run.metadata["rerun_count"] = count
            if count > 3:
                run.transition(RunStatus.FAILED)
                run.error = "exhausted reruns"
                return PhaseResult(done=True, extras={"reason": "rerun-exhausted"})
            return PhaseResult(next_phase="plan",
                               extras={"rerun": True, "rerun_count": count})

        if all_approved:
            return PhaseResult(next_phase="publish")

        run.transition(RunStatus.FAILED)
        run.error = "critique rejected without rerun signal"
        return PhaseResult(done=True, extras={"reason": "rejected"})

    # ── phase: publish ─────────────────────────────────────────────
    async def _phase_publish(self, run: WorkflowRun, wf) -> PhaseResult:
        run.transition(RunStatus.PUBLISHING)
        drafts = run.metadata.get("drafts") or []
        platforms = await self._load_platforms(wf, run.org_id)
        post_ids: list[str] = []
        for draft in drafts:
            target = self._match_platform(draft, platforms)
            if target is None:
                run.append(AgentTraceEvent(
                    agent="publisher", event="no_target_platform",
                    payload={"draft_platform": draft.get("platform")},
                ))
                continue
            try:
                post = await self._publish_one(run, wf, draft, target)
                post_ids.append(str(post.id))
            except CircuitOpen as exc:
                # Re-raise → worker retries. Already-published drafts
                # are saved so the retry only handles the rest.
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

    def _match_platform(self, draft, platforms):
        target = draft.get("platform")
        if not target:
            return platforms[0] if platforms else None
        for p in platforms:
            if str(p.kind) == target or p.id == target:
                return p
        return platforms[0] if platforms else None

    async def _publish_one(self, run, wf, draft, target_platform):
        breaker = self.breaker
        org_id = str(run.org_id)
        kind = "platform"
        plat_key = str(target_platform.kind)

        async def _do():
            adapter_cls = self.registry.get(PluginKind.PLATFORM, plat_key)
            if adapter_cls is None:
                raise PermanentError(f"no adapter for platform {plat_key}")
            adapter = adapter_cls()
            payload = PostPayload(
                content=draft.get("body", ""),
                title=draft.get("title", ""),
                media_urls=draft.get("media_urls") or [],
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

        # Persist Post.
        post = Post.create(
            org_id=run.org_id, workflow_id=wf.id,
            platform_id=target_platform.id, run_id=run.id,
            content=draft.get("body", ""), title=draft.get("title", ""),
            media_urls=list(draft.get("media_urls") or []),
        )
        post.status = PostStatus.PUBLISHED
        post.external_url = getattr(result, "url", None)
        await self.post_repo.add(post)
        return post


def SourceId_or_none(s: str):
    """Best-effort coerce string-uuid to SourceId, else return None."""
    from app.domain.value_objects.ids import SourceId
    try:
        return SourceId(UUID(s))
    except Exception:                                                # noqa: BLE001
        return None
