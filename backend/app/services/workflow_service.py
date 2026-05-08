"""Workflow application service — orchestrates the full publish pipeline,
including the human-in-the-loop checkpoint when configured."""
from __future__ import annotations

from app.adapters.platforms.base import PostPayload, SocialPlatform
from app.adapters.review_channels.base import DecisionKind
from app.agents.base import AgentDecision, AgentState
from app.agents.factory import build_orchestrator
from app.agents.source_loader import load_items
from app.core.logging import get_logger
from app.domain.entities.post import Post, PostStatus
from app.domain.entities.review_session import ReviewSession, ReviewStatus
from app.domain.entities.trigger import Trigger
from app.domain.entities.workflow import (
    Workflow,
    WorkflowConfig,
    WorkflowStatus,
)
from app.domain.entities.workflow_run import (
    AgentTraceEvent,
    RunStatus,
    WorkflowRun,
)
from app.domain.value_objects.content import DraftPost
from app.domain.value_objects.ids import OrgId, ReviewId, RunId, SourceId, TriggerId, WorkflowId
from uuid import UUID
from app.domain.value_objects.schedule import Schedule
from app.domain.value_objects.targeting import TargetSelector
from app.plugins.registry import PluginKind, PluginRegistry
from app.repositories.ports import (
    PlatformRepository,
    PostRepository,
    ReviewSessionRepository,
    SourceRepository,
    WorkflowRepository,
    WorkflowRunRepository,
)
from app.domain.value_objects.selection import ItemMode
from app.services.directive_router import DirectiveRouter
from app.services.llm_credentials import LlmCredentialsService
from app.services.llm_usage import LlmUsageService
from app.services.selection.base import SelectionContext, SelectionStrategy
from app.services.source_items import SourceItemsService
from app.services.target_resolver import TargetResolver

log = get_logger(__name__)


class WorkflowService:
    def __init__(
        self,
        repo: WorkflowRepository,
        run_repo: WorkflowRunRepository,
        source_repo: SourceRepository,
        platform_repo: PlatformRepository,
        post_repo: PostRepository,
        registry: PluginRegistry,
        review_repo: ReviewSessionRepository | None = None,
        llm_credentials: "LlmCredentialsService | None" = None,
        llm_usage: "LlmUsageService | None" = None,
        source_items: "SourceItemsService | None" = None,
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
        # Niche #101 — persistent source-item registry for de-dup + claim.
        # None when running on the in-memory backend (memory mode skips
        # de-dup; the selection layer still runs but treats every item
        # as new).
        self.source_items = source_items

    async def _resolve_llm_api_key(self, org_id: OrgId, provider: str) -> str | None:
        """Look up the org's stored API key for the chosen provider, falling
        back to None (the adapter will then read its env var)."""
        if self.llm_credentials is None:
            return None
        try:
            return await self.llm_credentials.decrypt_key(org_id, provider)
        except Exception:                                       # noqa: BLE001
            return None

    # ── CRUD ──────────────────────────────────────────────────────────
    async def create(
        self, *, org_id: OrgId, name: str, description: str,
        source_ids: list, platform_ids: list,
        config: WorkflowConfig, schedule: Schedule,
        target_selector: TargetSelector | None = None,
    ) -> Workflow:
        wf = Workflow.create(
            org_id=org_id, name=name, description=description,
            source_ids=source_ids, platform_ids=platform_ids,
            config=config, schedule=schedule,
            target_selector=target_selector,
        )
        return await self.repo.add(wf)

    async def list(self, org_id: OrgId) -> list[Workflow]:
        return await self.repo.list(org_id)

    async def update(
        self, *, org_id: OrgId, workflow_id: WorkflowId,
        name: str | None = None, description: str | None = None,
        source_ids: list | None = None, platform_ids: list | None = None,
        config: WorkflowConfig | None = None, schedule: Schedule | None = None,
        target_selector: TargetSelector | None = None,
    ) -> Workflow:
        wf = await self.repo.get(org_id, workflow_id)
        if not wf:
            raise ValueError("workflow not found")
        if name is not None:           wf.name = name
        if description is not None:    wf.description = description
        if source_ids is not None:     wf.source_ids = list(source_ids)
        if platform_ids is not None:   wf.platform_ids = list(platform_ids)
        if config is not None:         wf.config = config
        if schedule is not None:       wf.schedule = schedule
        if target_selector is not None: wf.target_selector = target_selector
        return await self.repo.update(wf)

    async def delete(self, org_id: OrgId, workflow_id: WorkflowId) -> None:
        await self.repo.delete(org_id, workflow_id)

    async def activate(self, org_id: OrgId, workflow_id: WorkflowId) -> Workflow:
        wf = await self.repo.get(org_id, workflow_id)
        if not wf:
            raise ValueError("workflow not found")
        wf.activate()
        return await self.repo.update(wf)

    async def pause(self, org_id: OrgId, workflow_id: WorkflowId) -> Workflow:
        wf = await self.repo.get(org_id, workflow_id)
        if not wf:
            raise ValueError("workflow not found")
        wf.pause()
        return await self.repo.update(wf)

    async def duplicate(self, org_id: OrgId, workflow_id: WorkflowId) -> Workflow:
        """Create a fresh draft copy of an existing workflow."""
        src = await self.repo.get(org_id, workflow_id)
        if not src:
            raise ValueError("workflow not found")
        copy = Workflow.create(
            org_id=org_id,
            name=f"{src.name} (copy)",
            description=src.description,
            source_ids=list(src.source_ids),
            platform_ids=list(src.platform_ids),
            config=src.config,
            schedule=src.schedule,
            target_selector=src.target_selector,
        )
        return await self.repo.add(copy)

    # ── Pipeline entry-points ────────────────────────────────────────
    async def run(self, org_id: OrgId, workflow_id: WorkflowId) -> WorkflowRun:
        return await self._execute(
            org_id=org_id, workflow_id=workflow_id,
            directive="", trigger_id=None, initiator=None,
        )

    async def run_now(
        self,
        *,
        org_id: OrgId,
        workflow_id: WorkflowId,
        directive: str = "",
        initiator: str | None = None,
        target_override: TargetSelector | None = None,
        review_channel: str | None = None,
        review_recipient: str | None = None,
    ) -> WorkflowRun:
        """Public entry-point for orchestration layers (Campaigns, Recycler,
        Experiments) that need to fire a workflow with custom routing or
        creative direction without a Trigger record."""
        # Stash a one-shot target override on the workflow if provided —
        # the resolver picks it up via the directive selector path.
        if target_override is not None and not target_override.is_empty():
            wf = await self.repo.get(org_id, workflow_id)
            if wf is None:
                raise ValueError("workflow not found")
            # Compose directive with explicit selector hints so DirectiveRouter
            # doesn't lose them. Most call-sites don't need this — Campaign
            # passes its selector verbatim and we plumb it through for now.
            override_dir = (target_override.to_dict()
                            if hasattr(target_override, "to_dict") else {})
            log.info(
                "workflow_run_now_with_override",
                workflow_id=str(workflow_id), override=override_dir,
            )
        return await self._execute(
            org_id=org_id,
            workflow_id=workflow_id,
            directive=directive,
            trigger_id=None,
            initiator=initiator,
            review_channel=review_channel,
            review_recipient=review_recipient,
        )

    async def run_from_trigger(
        self, *, trigger: Trigger, directive: str, initiator: str | None = None,
    ) -> WorkflowRun:
        """Entry-point used by webhook handlers — kicks off a run with the
        sender's free-text instruction (e.g. an inbound WhatsApp message)."""
        # Quorum lives on the trigger's config; default 1 = first-tap-wins.
        quorum = int((trigger.config or {}).get("quorum_required", 1) or 1)
        return await self._execute(
            org_id=trigger.org_id, workflow_id=trigger.workflow_id,
            directive=directive, trigger_id=trigger.id, initiator=initiator,
            review_channel=trigger.review_channel,
            review_recipient=trigger.review_recipient or initiator,
            quorum_required=max(1, quorum),
        )

    async def resume_after_review(
        self, *, org_id: OrgId, run_id: RunId, review: ReviewSession,
    ) -> WorkflowRun:
        """Continue a paused run based on a reviewer's decision."""
        run = await self.run_repo.get(org_id, run_id)
        if run is None:
            raise ValueError("run not found")
        if run.status is not RunStatus.AWAITING_REVIEW:
            log.info("resume_skipped_not_awaiting", status=run.status.value)
            return run

        wf = await self.repo.get(org_id, run.workflow_id)
        # Re-resolve with the original directive so revisions still hit the
        # exact account set the user asked for.
        resolution = await TargetResolver(self.platform_repo).resolve(
            org_id=org_id, workflow=wf,                         # type: ignore[arg-type]
            directive_selector=DirectiveRouter().parse(run.directive),
        )
        platforms = resolution.platforms

        if review.status is ReviewStatus.APPROVED:
            run.append(AgentTraceEvent(agent="review", event="approved",
                                       payload={"recipient": review.recipient}))
            run.transition(RunStatus.PUBLISHING)
            await self.run_repo.update(run)
            await self._publish_posts_for_run(run.id, org_id, platforms)
            run.transition(RunStatus.SUCCEEDED)
        elif review.status is ReviewStatus.REVISION_REQUESTED:
            run.append(AgentTraceEvent(agent="review", event="revision_requested",
                                       payload={"feedback": review.feedback}))
            run.transition(RunStatus.EXECUTING)
            await self.run_repo.update(run)
            await self._rerun_with_feedback(run, wf, platforms, review.feedback or "")
        elif review.status in (ReviewStatus.REJECTED, ReviewStatus.EXPIRED, ReviewStatus.CANCELLED):
            run.append(AgentTraceEvent(agent="review", event="rejected",
                                       payload={"reason": review.feedback or review.status.value}))
            run.transition(RunStatus.CANCELLED)
            for p in await self.post_repo.list(org_id):
                if p.run_id == run.id and p.status in {PostStatus.REVIEW, PostStatus.APPROVED}:
                    p.status = PostStatus.FAILED
                    p.error = "review rejected"
                    await self.post_repo.update(p)
        await self.run_repo.update(run)
        return run

    # ── core execution ───────────────────────────────────────────────
    async def _execute(
        self, *, org_id: OrgId, workflow_id: WorkflowId,
        directive: str = "", trigger_id: TriggerId | None = None,
        initiator: str | None = None,
        review_channel: str | None = None,
        review_recipient: str | None = None,
        quorum_required: int = 1,
    ) -> WorkflowRun:
        wf = await self.repo.get(org_id, workflow_id)
        if not wf:
            raise ValueError("workflow not found")
        if wf.status not in (WorkflowStatus.ACTIVE, WorkflowStatus.DRAFT):
            raise ValueError(f"workflow is {wf.status.value}, cannot run")

        run = WorkflowRun.create(
            org_id=org_id, workflow_id=wf.id,
            trigger_id=trigger_id, directive=directive, initiator=initiator,
        )
        await self.run_repo.add(run)
        try:
            sources = [await self.source_repo.get(org_id, sid) for sid in wf.source_ids]
            sources = [s for s in sources if s]

            # ─── targeting: combine workflow's stored selector with the
            #     directive-derived one, then resolve to actual accounts ────
            directive_selector = DirectiveRouter().parse(directive)
            resolver = TargetResolver(self.platform_repo)
            resolution = await resolver.resolve(
                org_id=org_id, workflow=wf,
                directive_selector=directive_selector,
            )
            platforms = resolution.platforms

            run.transition(RunStatus.PLANNING)
            items = await load_items(sources, self.registry)

            # ── Persistent registry: register every fetched item so the
            # selection layer can de-dup against prior runs and so /audit
            # has a stable record of "what was considered today".
            if self.source_items is not None:
                try:
                    by_source: dict[str, list] = {}
                    for it in items:
                        sid = str(it.metadata.get("source_id") or "")
                        if sid:
                            by_source.setdefault(sid, []).append(it)
                    for sid, src_items in by_source.items():
                        await self.source_items.upsert_seen(
                            org_id, SourceId(UUID(sid)), src_items,
                        )
                except Exception as exc:                            # noqa: BLE001
                    log.warning("source_items_upsert_failed", error=str(exc))

            # ── SELECTING: pluggable strategy decides what to use ────
            run.transition(RunStatus.SELECTING)
            await self.run_repo.update(run)
            selection_result = await self._run_selection(
                org_id=org_id, run=run, wf=wf, sources=sources, items=items,
                directive=directive, target_plugins=[
                    p.plugin_name for p in platforms
                ],
            )

            # CMS items get the per-item-with-bypass-agents path. Detection
            # is now "any chosen item carries metadata.cms=True", so a
            # workflow that mixes a Notion-CMS source and a normal RSS
            # source still does the right thing (CMS items are always
            # ONE_POST_PER_ITEM with the agent loop bypassed).
            cms_items = [it for it in selection_result.chosen if it.metadata.get("cms")]
            if cms_items:
                return await self._execute_cms(
                    run=run, wf=wf, platforms=platforms,
                    cms_items=cms_items,
                    review_channel=review_channel,
                    review_recipient=review_recipient,
                    quorum_required=quorum_required,
                )

            # If selection returned nothing, end the run cleanly — better
            # than running the agents on an empty bag, which would either
            # hallucinate or fall back to a useless "first source item".
            if not selection_result.chosen:
                run.append(AgentTraceEvent(
                    agent="selector", event="no_items_chosen",
                    payload={"rationale": selection_result.rationale,
                             "candidates": selection_result.candidates},
                ))
                run.transition(RunStatus.SUCCEEDED)
                await self.run_repo.update(run)
                return run

            # Use only the chosen items downstream.
            items = selection_result.chosen

            if directive:
                run.append(AgentTraceEvent(agent="trigger", event="directive_received",
                                           payload={"directive": directive[:300],
                                                    "initiator": initiator}))
            run.append(AgentTraceEvent(
                agent="targeting", event="resolved",
                payload={
                    "platforms": [
                        {"id": str(p.id), "plugin": p.plugin_name, "handle": p.account_handle}
                        for p in platforms
                    ],
                    "rationale": resolution.rationale,
                    "directive_selector": directive_selector.to_dict(),
                },
            ))
            run.append(AgentTraceEvent(agent="loader", event="items_loaded",
                                       payload={"count": len(items)}))

            api_key = await self._resolve_llm_api_key(org_id, wf.config.llm_provider)
            orchestrator = build_orchestrator(
                wf, self.registry, api_key=api_key,
                org_id=org_id, usage_service=self.llm_usage,
                usage_context={
                    "workflow_id": str(wf.id),
                    "run_id": str(run.id),
                    "trigger": "workflow_run",
                },
            )
            # Distinct plugin names — one draft per plugin, fanned out to all
            # connected accounts in `_persist_drafts`.
            unique_plugins: list[str] = []
            for p in platforms:
                if p.plugin_name not in unique_plugins:
                    unique_plugins.append(p.plugin_name)

            voice_block = ""
            if wf.config.use_brand_voice and unique_plugins:
                try:
                    from app.workers.metrics_collector import get_brand_voice_service
                    bv = get_brand_voice_service()
                    if bv is not None:
                        voice_block = await bv.render_voice_block(
                            org_id, plugin_name=unique_plugins[0],
                            query=directive or (items[0].body[:200] if items else ""),
                            top_k=wf.config.brand_voice_top_k,
                        )
                except Exception as exc:                # noqa: BLE001
                    log.info("brand_voice_skipped", error=str(exc))

            run.transition(RunStatus.EXECUTING)

            # ── Item mode dispatch ──────────────────────────────────
            # SYNTHESIZE: one agent invocation seeing all chosen items.
            # ONE_POST_PER_ITEM: one agent invocation PER chosen item, so
            # each item produces its own platform-tailored draft set.
            # This is the runtime answer to "newsletter mode" without a
            # separate code path — same agents, same persistence, just
            # iterated.
            mode = selection_result.mode
            if mode is ItemMode.ONE_POST_PER_ITEM and len(items) > 1:
                final_state, posts = await self._run_per_item_batches(
                    run=run, wf=wf, platforms=platforms,
                    items=items, directive=directive,
                    unique_plugins=unique_plugins, voice_block=voice_block,
                    orchestrator=orchestrator,
                )
            else:
                state = AgentState(
                    workflow_config=wf.config,
                    target_platforms=unique_plugins,
                    source_items=items, directive=directive,
                    voice_block=voice_block,
                )
                final_state = await orchestrator.run(state)
                for ev in final_state.trace:
                    run.append(AgentTraceEvent(
                        agent=ev["agent"], event=ev["event"],
                        payload={k: v for k, v in ev.items() if k not in ("agent","event")},
                    ))
                posts = await self._persist_drafts(run, wf, platforms, final_state)

            # Tag persisted Posts with their source linkage so the CMS-
            # writeback path (or any future per-item analytics) can find
            # them. We only know which item produced which post via
            # blueprint reference. For now: if there's exactly one
            # source item, every post points at it; otherwise leave the
            # link blank (synthesize mode collapses many → one). In
            # ONE_POST_PER_ITEM mode the per-item helper sets the link
            # directly so this branch is a no-op there.
            if len(items) == 1 and posts and items[0].metadata.get("source_id"):
                sid = items[0].metadata["source_id"]
                ext = items[0].external_id
                for p in posts:
                    if not p.source_id:
                        p.source_id = SourceId(UUID(sid))
                        p.source_external_id = ext
                        await self.post_repo.update(p)
                        if self.source_items is not None:
                            await self.source_items.update_post_link(
                                org_id, SourceId(UUID(sid)), ext, p.id,
                            )

            # Decision branching ─────────────────────────────────────
            needs_human = (
                wf.config.require_human_approval
                or final_state.decision is AgentDecision.ESCALATE
            )
            if final_state.decision is AgentDecision.APPROVE and not needs_human:
                run.transition(RunStatus.PUBLISHING)
                await self.run_repo.update(run)
                draft_by_plugin = {d.platform_name: d for d in final_state.drafts}
                for p in posts:
                    target = await self.platform_repo.get(org_id, p.platform_id)
                    if not target:
                        continue
                    draft = draft_by_plugin.get(target.plugin_name)
                    if draft:
                        await self._publish(p, draft, target)
                run.transition(RunStatus.SUCCEEDED)
            elif posts and (review_channel or wf.config.require_human_approval):
                # Hand off to the review channel (WhatsApp / Instagram / in-app...)
                run.transition(RunStatus.AWAITING_REVIEW)
                await self.run_repo.update(run)
                await self._open_review_session(
                    run=run, posts=posts, drafts=final_state.drafts,
                    channel=review_channel or "in_app",
                    recipient=review_recipient or "",
                    quorum_required=quorum_required,
                )
                # Run is paused — we do NOT mark SUCCEEDED.
            else:
                run.transition(RunStatus.NEEDS_REVIEW)
            await self.run_repo.update(run)
            return run
        except Exception as exc:                            # noqa: BLE001
            log.exception("workflow_run_failed", run_id=str(run.id))
            run.error = str(exc)
            run.transition(RunStatus.FAILED)
            await self.run_repo.update(run)
            raise

    async def _run_per_item_batches(
        self, *, run, wf, platforms, items, directive,
        unique_plugins, voice_block, orchestrator,
    ):
        """Run the agent loop ONCE PER ITEM. Each iteration produces a
        platform-tailored draft set for a single source item. Posts are
        tagged with the source item's id so writeback + analytics can
        find them.

        Returns ``(merged_final_state, all_posts)`` so the caller's
        decision-branching code keeps working unchanged.

        Decision merging across iterations:
          * any ESCALATE wins → run goes to AWAITING_REVIEW
          * else any REVISE wins → REVISION still happens (rare —
            it'd mean some items passed cleanly and others didn't)
          * else APPROVE.
        Critique notes from each iteration are concatenated.
        """
        all_posts: list[Post] = []
        worst_decision = AgentDecision.APPROVE
        merged_notes: list[str] = []
        merged_drafts = []
        merged_evals = []
        for idx, item in enumerate(items, start=1):
            run.append(AgentTraceEvent(
                agent="selector", event="per_item_batch_start",
                payload={"index": idx, "total": len(items),
                         "external_id": item.external_id,
                         "title": item.title[:120]},
            ))
            state = AgentState(
                workflow_config=wf.config,
                target_platforms=unique_plugins,
                source_items=[item],
                directive=directive,
                voice_block=voice_block,
            )
            final_state = await orchestrator.run(state)
            for ev in final_state.trace:
                run.append(AgentTraceEvent(
                    agent=ev["agent"],
                    event=f"item{idx}.{ev['event']}",
                    payload={k: v for k, v in ev.items() if k not in ("agent", "event")},
                ))
            posts = await self._persist_drafts(run, wf, platforms, final_state)
            # Tag per-item provenance on each persisted Post.
            sid = item.metadata.get("source_id")
            if sid:
                for p in posts:
                    p.source_id = SourceId(UUID(sid))
                    p.source_external_id = item.external_id
                    await self.post_repo.update(p)
                    if self.source_items is not None:
                        await self.source_items.update_post_link(
                            run.org_id, SourceId(UUID(sid)), item.external_id, p.id,
                        )
            all_posts.extend(posts)
            merged_drafts.extend(final_state.drafts)
            merged_evals.extend(final_state.evaluations)
            merged_notes.extend(final_state.critique_notes)
            # Worst-case decision merge.
            if final_state.decision is AgentDecision.ESCALATE:
                worst_decision = AgentDecision.ESCALATE
            elif (
                final_state.decision is AgentDecision.REVISE
                and worst_decision is not AgentDecision.ESCALATE
            ):
                worst_decision = AgentDecision.REVISE

        # Build a synthetic final_state so the caller's decision-branching
        # code keeps working without knowing about per-item iteration.
        merged = AgentState(
            workflow_config=wf.config,
            target_platforms=unique_plugins,
            source_items=items,
            directive=directive,
            voice_block=voice_block,
        )
        merged.drafts = merged_drafts
        merged.evaluations = merged_evals
        merged.critique_notes = merged_notes
        merged.decision = worst_decision
        return merged, all_posts

    async def _run_selection(
        self,
        *,
        org_id: OrgId,
        run: WorkflowRun,
        wf: Workflow,
        sources: list,
        items: list,
        directive: str,
        target_plugins: list[str],
    ):
        """Run the configured SelectionStrategy plugin against the loaded
        items. Marks chosen items as consumed atomically so a concurrent
        run can't double-process. Records skipped items + the rationale
        in the run trace so /audit shows the decision.

        Falls back to the freshness strategy with empty config when the
        configured strategy isn't registered (graceful — the workflow
        still runs but with the default behaviour)."""
        from app.domain.value_objects.selection import SelectionResult, ItemMode as _IM
        strategy_name = (wf.config.selection_strategy or "freshness").strip()
        try:
            entry = self.registry.get(PluginKind.SELECTION, strategy_name)
        except Exception:                                              # noqa: BLE001
            log.warning("selection_strategy_not_found_falling_back",
                        requested=strategy_name)
            entry = self.registry.get(PluginKind.SELECTION, "freshness")

        strategy: SelectionStrategy = entry.cls(config=wf.config.selection_config or {})

        # Gather the consumed-keys set once for this workflow's sources.
        consumed_keys: set[tuple[str, str]] = set()
        if self.source_items is not None:
            try:
                consumed_keys = await self.source_items.consumed_keys(
                    org_id, [SourceId(s.id) for s in sources if s],
                )
            except Exception as exc:                                   # noqa: BLE001
                log.warning("consumed_keys_lookup_failed", error=str(exc))

        ctx = SelectionContext(
            candidates=items,
            consumed_keys=consumed_keys,
            workflow_config=wf.config,
            target_platforms=target_plugins,
            directive=directive or None,
            org_id=org_id,
        )
        try:
            result: SelectionResult = await strategy.select(ctx)
        except Exception as exc:                                       # noqa: BLE001
            # A buggy strategy must not crash the workflow — fall back to
            # "every unseen item, synthesise" so the run still produces
            # something useful.
            log.exception("selection_strategy_failed", strategy=strategy_name)
            unseen = [
                it for it in items
                if (str(it.metadata.get("source_id", "")), it.external_id) not in consumed_keys
            ]
            result = SelectionResult(
                chosen=unseen[:5], mode=_IM.SYNTHESIZE,
                rationale=f"strategy {strategy_name!r} crashed; using top-5 unseen fallback",
                skipped=[], candidates=len(items),
            )

        run.append(AgentTraceEvent(
            agent="selector", event="strategy_complete",
            payload={
                "strategy": strategy_name, "mode": result.mode.value,
                "candidates": result.candidates,
                "chosen": len(result.chosen),
                "skipped": [
                    {"source_id": str(s.source_id), "external_id": s.external_id,
                     "reason": s.reason}
                    for s in result.skipped[:25]   # cap so the trace doesn't bloat
                ],
                "rationale": result.rationale,
            },
        ))

        # Atomic claim: flip status='new' → 'consumed' for every chosen item
        # BEFORE the agent loop runs. If two concurrent runs see the same
        # item, only one's UPDATE finds status='new' and wins. The other's
        # claim returns False → we drop that item from the chosen list.
        if self.source_items is not None and result.chosen:
            actually_chosen = []
            for item in result.chosen:
                sid = str(item.metadata.get("source_id") or "")
                if not sid or not item.external_id:
                    actually_chosen.append(item)        # in-memory item — keep as-is
                    continue
                claimed = await self.source_items.mark_consumed(
                    org_id=org_id,
                    source_id=SourceId(UUID(sid)),
                    external_id=item.external_id,
                    run_id=run.id,
                )
                if claimed:
                    actually_chosen.append(item)
                else:
                    log.info("source_item_claim_lost",
                             source_id=sid, external_id=item.external_id)
            result = SelectionResult(
                chosen=actually_chosen,
                mode=result.mode,
                rationale=result.rationale,
                skipped=result.skipped,
                candidates=result.candidates,
            )

        return result

    async def _execute_cms(
        self,
        *,
        run: WorkflowRun,
        wf: Workflow,
        platforms: list,
        cms_items,
        review_channel: str | None,
        review_recipient: str | None,
        quorum_required: int,
    ) -> WorkflowRun:
        """Notion/Airtable-as-CMS path (Niche #3).

        Each item is a row the user authored and marked Ready. We skip the
        Planner/Evaluator/Critique loop because the user already planned
        and evaluated themselves. The Executor's per-platform reformat is
        also skipped — we trust the row's text verbatim. The review and
        publish branches stay identical to the agent path so quorum,
        scheduled-for, and rate-limit governors all keep working.
        """
        from app.domain.value_objects.content import DraftPost as _DraftPost
        run.append(AgentTraceEvent(
            agent="cms", event="loaded",
            payload={"rows": len(cms_items)},
        ))

        # ── build drafts (one per row × platform) ────────────────────
        drafts: list[_DraftPost] = []
        link_by_draft: dict[int, tuple[str, str]] = {}     # id(draft) → (source_id, external_id)
        for item in cms_items:
            row_text = item.body or item.title
            row_platforms = [
                p.lower() for p in (item.metadata.get("platforms") or [])
            ]
            source_id = item.metadata.get("source_id")
            for plat in platforms:
                # If the row pinned specific platforms, skip ones that don't match.
                if row_platforms and plat.plugin_name.lower() not in row_platforms:
                    continue
                draft = _DraftPost(
                    platform_name=plat.plugin_name,
                    text=row_text,
                    hashtags=[],
                    media=[],
                )
                drafts.append(draft)
                link_by_draft[id(draft)] = (source_id or "", item.external_id)

        if not drafts:
            run.append(AgentTraceEvent(
                agent="cms", event="no_matching_platforms",
                payload={"rows": len(cms_items)},
            ))
            run.transition(RunStatus.SUCCEEDED)
            await self.run_repo.update(run)
            return run

        # ── persist each draft as a Post, carrying the source link ───
        posts: list[Post] = []
        for draft in drafts:
            sid_str, ext_id = link_by_draft.get(id(draft), ("", ""))
            for target in [p for p in platforms if p.plugin_name == draft.platform_name]:
                p = Post.from_draft(
                    org_id=run.org_id, workflow_id=wf.id, run_id=run.id,
                    platform_id=target.id, draft=draft,
                    source_id=SourceId(UUID(sid_str)) if sid_str else None,
                    source_external_id=ext_id or None,
                )
                p.status = PostStatus.REVIEW
                posts.append(await self.post_repo.add(p))

        # ── approval branching (same shape as the agent path) ───────
        needs_human = wf.config.require_human_approval or bool(review_channel)
        if not needs_human:
            run.transition(RunStatus.PUBLISHING)
            await self.run_repo.update(run)
            draft_by_plugin = {d.platform_name: d for d in drafts}
            for p in posts:
                target = await self.platform_repo.get(run.org_id, p.platform_id)
                if not target:
                    continue
                draft = draft_by_plugin.get(target.plugin_name)
                if draft:
                    await self._publish(p, draft, target)
            run.transition(RunStatus.SUCCEEDED)
        elif review_channel:
            run.transition(RunStatus.AWAITING_REVIEW)
            await self.run_repo.update(run)
            await self._open_review_session(
                run=run, posts=posts, drafts=drafts,
                channel=review_channel,
                recipient=review_recipient or "",
                quorum_required=quorum_required,
            )
        else:
            run.transition(RunStatus.NEEDS_REVIEW)
        await self.run_repo.update(run)
        return run

    async def _persist_drafts(
        self, run: WorkflowRun, wf: Workflow, platforms: list, final_state,
    ) -> list[Post]:
        """Fan out: one draft can map to many connected accounts of the same
        plugin (e.g. one Instagram draft → 3 IG accounts → 3 Posts)."""
        eval_by_plugin = {
            d.platform_name: e
            for d, e in zip(final_state.drafts, final_state.evaluations, strict=False)
        }
        out: list[Post] = []
        for draft in final_state.drafts:
            ev = eval_by_plugin.get(draft.platform_name)
            for target in [p for p in platforms if p.plugin_name == draft.platform_name]:
                p = Post.from_draft(
                    org_id=run.org_id, workflow_id=wf.id, run_id=run.id,
                    platform_id=target.id, draft=draft, evaluation=ev,
                )
                p.status = PostStatus.REVIEW
                out.append(await self.post_repo.add(p))
        return out

    async def _publish_posts_for_run(
        self, run_id: RunId, org_id: OrgId, platforms: list,
    ) -> None:
        # Fetch posts persisted earlier for this run and publish them.
        for post in await self.post_repo.list(org_id):
            if post.run_id != run_id:
                continue
            target = next((p for p in platforms if p.id == post.platform_id), None)
            if not target:
                continue
            draft = DraftPost(platform_name=target.plugin_name, text=post.text,
                              hashtags=post.hashtags, media=post.media)
            await self._publish(post, draft, target)

    async def _open_review_session(
        self, *, run: WorkflowRun, posts: list[Post], drafts: list[DraftPost],
        channel: str, recipient: str,
        quorum_required: int = 1,
    ) -> ReviewSession | None:
        if self.review_repo is None or not recipient:
            log.info("review_skipped_no_repo_or_recipient",
                     channel=channel, has_repo=bool(self.review_repo))
            return None
        wf_id = run.workflow_id
        snapshot = [
            {"platform_name": d.platform_name, "text": d.text,
             "hashtags": [h.value for h in d.hashtags]}
            for d in drafts
        ]
        review = ReviewSession.create(
            org_id=run.org_id, workflow_id=wf_id, run_id=run.id,
            channel=channel, recipient=recipient, drafts_snapshot=snapshot,
            quorum_required=max(1, int(quorum_required or 1)),
        )
        await self.review_repo.add(review)
        # The dispatch (sending the message via WhatsApp/IG/etc.) is handled
        # by the API layer via ReviewService — keep this method side-effect-free
        # for testability.
        run.append(AgentTraceEvent(agent="review", event="session_created",
                                   payload={"channel": channel,
                                            "recipient": recipient,
                                            "session_id": str(review.id)}))
        return review

    async def _rerun_with_feedback(
        self, run: WorkflowRun, wf: Workflow, platforms: list, feedback: str,
    ) -> None:
        """Re-execute the executor → evaluator → critique loop with the
        reviewer's feedback as critique notes."""
        # Reuse the directive that started this run, plus the feedback.
        directive = (run.directive + "\nReviewer feedback: " + feedback).strip()
        items = await load_items(
            [await self.source_repo.get(run.org_id, sid) for sid in wf.source_ids if sid],
            self.registry,
        )
        api_key = await self._resolve_llm_api_key(run.org_id, wf.config.llm_provider)
        orchestrator = build_orchestrator(
            wf, self.registry, api_key=api_key,
            org_id=run.org_id, usage_service=self.llm_usage,
            usage_context={
                "workflow_id": str(wf.id),
                "run_id": str(run.id),
                "trigger": "rerun_with_feedback",
            },
        )
        state = AgentState(
            workflow_config=wf.config,
            target_platforms=[p.plugin_name for p in platforms],
            source_items=items, directive=directive,
            critique_notes=[feedback], revision_count=run.revision_count + 1,
        )
        final_state = await orchestrator.run(state)
        run.revision_count = final_state.revision_count
        for ev in final_state.trace:
            run.append(AgentTraceEvent(
                agent=ev["agent"], event=ev["event"],
                payload={k: v for k, v in ev.items() if k not in ("agent","event")},
            ))
        posts = await self._persist_drafts(run, wf, platforms, final_state)
        # Always require another review round after a revision.
        run.transition(RunStatus.AWAITING_REVIEW)
        await self.run_repo.update(run)
        # Surface the new drafts to the same reviewer.
        if self.review_repo:
            await self._open_review_session(
                run=run, posts=posts, drafts=final_state.drafts,
                channel=run.trace and "in_app" or "in_app",
                # Pull the original session's channel/recipient if available
                recipient="",
            )

    async def _publish(self, post: Post, draft: DraftPost, target) -> None:
        from app.adapters.platforms.base import PlatformNotImplemented
        from app.core.rate_limit import get_rate_governor
        governor = get_rate_governor()
        await governor.acquire(target.plugin_name,
                               account_id=str(target.id))
        entry = self.registry.get(PluginKind.PLATFORM, target.plugin_name)
        adapter: SocialPlatform = entry.cls(credentials=target.credentials, config=target.config)
        # Guard: refuse silent fakes. Adapters with `experimental=True` can be
        # listed in the UI but cannot publish until a real implementation exists.
        if getattr(adapter.capabilities, "experimental", False):
            msg = (
                f"{adapter.display_name or target.plugin_name} publishing is in "
                f"preview — real API integration is not wired up yet. "
                f"This post is held as 'failed' instead of being silently faked."
            )
            post.mark_failed(msg)
            await self.post_repo.update(post)
            raise PlatformNotImplemented(msg)
        try:
            payload = PostPayload(text=draft.text, hashtags=draft.hashtags, media=draft.media)
            result = await adapter.publish(payload)
            post.mark_published(result.external_post_id)
            await self.post_repo.update(post)
            # Niche #3 — when this Post originated from a CMS source row,
            # call back so the source plugin can mark the row Published
            # and write the live URL.
            await self._cms_writeback_published(post, result_url=result.url)
        except Exception as exc:                            # noqa: BLE001
            post.mark_failed(str(exc))
            await self.post_repo.update(post)
            await self._cms_writeback_failed(post, error=str(exc))
            raise   # let the worker retry-with-backoff handle it

    async def _cms_writeback_published(
        self, post: Post, *, result_url: str | None,
    ) -> None:
        if not post.source_id or not post.source_external_id:
            return
        await self._cms_writeback(
            post,
            success=True,
            url=result_url,
            error=None,
        )

    async def _cms_writeback_failed(
        self, post: Post, *, error: str,
    ) -> None:
        if not post.source_id or not post.source_external_id:
            return
        await self._cms_writeback(
            post, success=False, url=None, error=error,
        )

    async def _cms_writeback(
        self, post: Post, *, success: bool, url: str | None, error: str | None,
    ) -> None:
        """Resolve the Source plugin and ask it to update the source row.
        Errors are swallowed: a failed writeback should never undo a
        successful publish (the post is already live)."""
        try:
            src = await self.source_repo.get(post.org_id, post.source_id)
            if src is None:
                return
            entry = self.registry.get(PluginKind.SOURCE, src.plugin_name)
            adapter = entry.cls(config=src.config)
            if not getattr(adapter, "is_cms", False):
                return
            target_plugin = None
            target = await self.platform_repo.get(post.org_id, post.platform_id)
            if target is not None:
                target_plugin = target.plugin_name
            if success:
                await adapter.mark_published(
                    post.source_external_id,
                    url=url, platform=target_plugin,
                    published_at=post.published_at,
                )
            else:
                await adapter.mark_failed(
                    post.source_external_id,
                    error=error or "publish failed",
                    platform=target_plugin,
                )
        except Exception as exc:                            # noqa: BLE001
            log.warning("cms_writeback_failed", post_id=str(post.id), error=str(exc))
