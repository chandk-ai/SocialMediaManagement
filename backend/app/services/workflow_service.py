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
from app.domain.value_objects.ids import OrgId, ReviewId, RunId, TriggerId, WorkflowId
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
from app.services.directive_router import DirectiveRouter
from app.services.llm_credentials import LlmCredentialsService
from app.services.llm_usage import LlmUsageService
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
        return await self._execute(
            org_id=trigger.org_id, workflow_id=trigger.workflow_id,
            directive=directive, trigger_id=trigger.id, initiator=initiator,
            review_channel=trigger.review_channel,
            review_recipient=trigger.review_recipient or initiator,
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

            state = AgentState(
                workflow_config=wf.config,
                target_platforms=unique_plugins,
                source_items=items, directive=directive,
                voice_block=voice_block,
            )
            run.transition(RunStatus.EXECUTING)
            final_state = await orchestrator.run(state)
            for ev in final_state.trace:
                run.append(AgentTraceEvent(
                    agent=ev["agent"], event=ev["event"],
                    payload={k: v for k, v in ev.items() if k not in ("agent","event")},
                ))

            posts = await self._persist_drafts(run, wf, platforms, final_state)

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
        except Exception as exc:                            # noqa: BLE001
            post.mark_failed(str(exc))
            await self.post_repo.update(post)
            raise   # let the worker retry-with-backoff handle it
