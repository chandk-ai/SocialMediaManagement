"""ContentRecyclerService — re-publishes evergreen, top-performing posts
on a configurable cadence with optional LLM-driven freshening.

Pairs with `RecyclePolicy` (the configuration aggregate) and is invoked
from a Celery beat job (see `app/workers/content_recycler.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.adapters.llm.base import LLMProvider, LLMRequest
from app.core.logging import get_logger
from app.domain.entities.post import Post, PostStatus
from app.domain.entities.recycle_policy import RecyclePolicy, RecycleStrategy
from app.domain.value_objects.content import DraftPost, Hashtag
from app.domain.value_objects.ids import (
    OrgId,
    PostId,
    RecyclePolicyId,
)
from app.repositories.ports import (
    PlatformRepository,
    PostRepository,
    RecyclePolicyRepository,
    WorkflowRepository,
)
from app.services.workflow_service import WorkflowService

log = get_logger(__name__)


@dataclass(slots=True)
class RecycleCandidate:
    post: Post
    score: float
    recycle_count: int
    rationale: str


@dataclass(slots=True)
class RecycleOutcome:
    policy_id: RecyclePolicyId
    candidate_post_id: PostId
    new_post_id: PostId | None
    status: str          # "republished" | "skipped" | "failed"
    detail: str


class ContentRecyclerService:
    def __init__(
        self,
        repo: RecyclePolicyRepository,
        post_repo: PostRepository,
        platform_repo: PlatformRepository,
        wf_repo: WorkflowRepository,
        wf_service: WorkflowService,
        llm: LLMProvider | None = None,
    ) -> None:
        self.repo = repo
        self.post_repo = post_repo
        self.platform_repo = platform_repo
        self.wf_repo = wf_repo
        self.wf_service = wf_service
        self.llm = llm

    # ── CRUD ──────────────────────────────────────────────────────────
    async def create_policy(
        self,
        *,
        org_id: OrgId,
        name: str,
        strategy: RecycleStrategy = RecycleStrategy.LIGHT_REWRITE,
        cooldown_days: int = 30,
        max_recycle_count: int = 3,
        cadence_days: int = 14,
        min_engagement_score: float = 0.4,
        workflow_ids: list | None = None,
        platform_ids: list | None = None,
    ) -> RecyclePolicy:
        p = RecyclePolicy.create(
            org_id=org_id,
            name=name,
            strategy=strategy,
            cooldown_days=cooldown_days,
            max_recycle_count=max_recycle_count,
            cadence_days=cadence_days,
            min_engagement_score=min_engagement_score,
            workflow_ids=workflow_ids,
            platform_ids=platform_ids,
        )
        return await self.repo.add(p)

    async def list_policies(self, org_id: OrgId) -> list[RecyclePolicy]:
        return await self.repo.list(org_id)

    async def disable(
        self, org_id: OrgId, policy_id: RecyclePolicyId,
    ) -> RecyclePolicy:
        p = await self.repo.get(org_id, policy_id)
        if not p:
            raise ValueError("policy not found")
        p.enabled = False
        return await self.repo.update(p)

    # ── Pipeline ──────────────────────────────────────────────────────
    async def find_candidates(
        self, *, policy: RecyclePolicy, now: datetime | None = None,
    ) -> list[RecycleCandidate]:
        now = now or datetime.utcnow()
        cutoff_old = now - timedelta(days=policy.cooldown_days)
        candidates: list[RecycleCandidate] = []
        published = await self.post_repo.list(
            policy.org_id, status=PostStatus.PUBLISHED.value,
        )
        for p in published:
            if not policy.covers_workflow(p.workflow_id):
                continue
            if not policy.covers_platform(p.platform_id):
                continue
            if p.published_at is None or p.published_at > cutoff_old:
                continue
            recycle_count = int((p.metrics or {}).get("recycle_count", 0))
            if recycle_count >= policy.max_recycle_count:
                continue
            score = _engagement(p.metrics)
            if score < policy.min_engagement_score:
                continue
            candidates.append(RecycleCandidate(
                post=p, score=score, recycle_count=recycle_count,
                rationale=(
                    f"engagement {score:.2f} ≥ {policy.min_engagement_score:.2f}, "
                    f"published {(now - p.published_at).days}d ago, "
                    f"recycled {recycle_count}/{policy.max_recycle_count}"
                ),
            ))
        # Highest score wins.
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    async def execute_policy(
        self,
        *,
        org_id: OrgId,
        policy_id: RecyclePolicyId,
        max_per_run: int = 3,
        now: datetime | None = None,
    ) -> list[RecycleOutcome]:
        policy = await self.repo.get(org_id, policy_id)
        if not policy:
            raise ValueError("policy not found")
        if not policy.enabled:
            return []
        outcomes: list[RecycleOutcome] = []
        candidates = await self.find_candidates(policy=policy, now=now)
        for c in candidates[:max_per_run]:
            outcome = await self._republish(policy, c)
            outcomes.append(outcome)
        policy.mark_ran()
        await self.repo.update(policy)
        return outcomes

    async def execute_due_policies(
        self, *, now: datetime | None = None, max_per_policy: int = 3,
    ) -> list[RecycleOutcome]:
        now = now or datetime.utcnow()
        outcomes: list[RecycleOutcome] = []
        for policy in await self.repo.list_due(now):
            try:
                outs = await self.execute_policy(
                    org_id=policy.org_id,
                    policy_id=policy.id,
                    max_per_run=max_per_policy,
                    now=now,
                )
                outcomes.extend(outs)
            except Exception as exc:                              # noqa: BLE001
                log.exception(
                    "recycle_policy_failed",
                    policy_id=str(policy.id), error=str(exc),
                )
        return outcomes

    # ── internal helpers ──────────────────────────────────────────────
    async def _republish(
        self, policy: RecyclePolicy, candidate: RecycleCandidate,
    ) -> RecycleOutcome:
        original = candidate.post
        platform = await self.platform_repo.get(policy.org_id, original.platform_id)
        if not platform:
            return RecycleOutcome(
                policy_id=policy.id,
                candidate_post_id=original.id,
                new_post_id=None,
                status="skipped",
                detail="platform missing",
            )
        try:
            new_text = await self._refresh_text(original, policy.strategy)
            draft = DraftPost(
                platform_name=platform.plugin_name,
                text=new_text,
                hashtags=list(original.hashtags),
                media=list(original.media),
            )
            new_post = Post.from_draft(
                org_id=policy.org_id,
                workflow_id=original.workflow_id,
                run_id=original.run_id,
                platform_id=original.platform_id,
                draft=draft,
            )
            metrics = dict(original.metrics or {})
            metrics["recycle_count"] = candidate.recycle_count + 1
            metrics["recycled_from"] = str(original.id)
            new_post.metrics = metrics
            new_post.status = PostStatus.SCHEDULED
            await self.post_repo.add(new_post)
            return RecycleOutcome(
                policy_id=policy.id,
                candidate_post_id=original.id,
                new_post_id=new_post.id,
                status="republished",
                detail=f"strategy={policy.strategy.value}",
            )
        except Exception as exc:                                 # noqa: BLE001
            log.exception(
                "recycle_republish_failed",
                policy_id=str(policy.id),
                source_post=str(original.id),
                error=str(exc),
            )
            return RecycleOutcome(
                policy_id=policy.id,
                candidate_post_id=original.id,
                new_post_id=None,
                status="failed",
                detail=str(exc),
            )

    async def _refresh_text(self, post: Post, strategy: RecycleStrategy) -> str:
        if strategy is RecycleStrategy.REPOST_VERBATIM or self.llm is None:
            return post.text
        if strategy is RecycleStrategy.LIGHT_REWRITE:
            prompt = (
                "Rewrite the following social-media post to feel fresh while "
                "preserving the core message, tone, hashtags and call-to-action.\n\n"
                f"Original:\n{post.text}\n\nRewrite:"
            )
        elif strategy is RecycleStrategy.FULL_REGEN:
            prompt = (
                "Generate a brand-new social-media post that delivers the same "
                "key insight as the one below. Use a different opening hook and "
                "structure, but keep the brand voice consistent.\n\n"
                f"Original:\n{post.text}\n\nNew post:"
            )
        elif strategy is RecycleStrategy.THREAD_FROM_TOP:
            prompt = (
                "Convert this single post into a 3-tweet thread. Number each "
                "tweet 1/3, 2/3, 3/3. Preserve the original key message in "
                "tweet 2.\n\n"
                f"Original:\n{post.text}\n\nThread:"
            )
        else:
            return post.text
        try:
            req = LLMRequest(
                system="You are an editor refreshing high-performing social posts.",
                prompt=prompt,
                temperature=0.7,
                max_tokens=600,
            )
            res = await self.llm.complete(req)
            return res.text.strip() or post.text
        except Exception as exc:                                  # noqa: BLE001
            log.warning("recycle_llm_refresh_failed", error=str(exc))
            return post.text


def _engagement(metrics: dict | None) -> float:
    if not metrics:
        return 0.0
    for k in ("engagement_rate", "engagement_pct", "interaction_rate"):
        v = metrics.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    likes = metrics.get("likes") or metrics.get("favorites") or 0
    impressions = metrics.get("impressions") or metrics.get("views") or 0
    if impressions:
        return float(likes) / float(impressions)
    return 0.0
