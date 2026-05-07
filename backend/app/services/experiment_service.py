"""ExperimentService — A/B/n variant testing.

Lifecycle:
  1. `create()` — caller produces n `Variant`s (typically by calling the
     LLM `n` times with different style hints).
  2. `start()` — each variant is published as its own `Post` and wired to
     the experiment.
  3. `collect_metrics()` — usually invoked by the metrics-collector worker
     once per variant after the settling window.
  4. `settle()` — pick a winner and freeze the experiment state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.adapters.platforms.base import PostPayload
from app.core.logging import get_logger
from app.domain.entities.experiment import (
    AllocationKind,
    Experiment,
    ExperimentStatus,
    Variant,
    VariantStatus,
)
from app.domain.entities.post import Post, PostStatus
from app.domain.value_objects.content import DraftPost, Hashtag
from app.domain.value_objects.ids import (
    ExperimentId,
    OrgId,
    PlatformId,
    PostId,
    RunId,
    VariantId,
    WorkflowId,
)
from app.plugins.registry import PluginKind, PluginRegistry
from app.repositories.ports import (
    ExperimentRepository,
    PlatformRepository,
    PostRepository,
)

log = get_logger(__name__)


class ExperimentService:
    def __init__(
        self,
        repo: ExperimentRepository,
        post_repo: PostRepository,
        platform_repo: PlatformRepository,
        registry: PluginRegistry,
    ) -> None:
        self.repo = repo
        self.post_repo = post_repo
        self.platform_repo = platform_repo
        self.registry = registry

    # ── CRUD ──────────────────────────────────────────────────────────
    async def create(
        self,
        *,
        org_id: OrgId,
        workflow_id: WorkflowId,
        platform_id: PlatformId,
        hypothesis: str,
        variants: list[Variant],
        metric: str = "engagement_rate",
        settling_minutes: int = 60 * 24,
        allocation: AllocationKind = AllocationKind.EQUAL,
        extras: dict | None = None,
    ) -> Experiment:
        normalized = _normalize_weights(variants, allocation)
        e = Experiment.create(
            org_id=org_id,
            workflow_id=workflow_id,
            platform_id=platform_id,
            hypothesis=hypothesis,
            variants=normalized,
            metric=metric,
            settling_minutes=settling_minutes,
            allocation=allocation,
            extras=extras,
        )
        return await self.repo.add(e)

    async def list(
        self, org_id: OrgId, *, status: str | None = None,
    ) -> list[Experiment]:
        return await self.repo.list(org_id, status=status)

    async def get(self, org_id: OrgId, experiment_id: ExperimentId) -> Experiment:
        e = await self.repo.get(org_id, experiment_id)
        if not e:
            raise ValueError("experiment not found")
        return e

    async def cancel(
        self, org_id: OrgId, experiment_id: ExperimentId, reason: str | None = None,
    ) -> Experiment:
        e = await self.get(org_id, experiment_id)
        e.cancel(reason)
        return await self.repo.update(e)

    # ── Pipeline ──────────────────────────────────────────────────────
    async def start(
        self,
        *,
        org_id: OrgId,
        experiment_id: ExperimentId,
        run_id: RunId,
    ) -> Experiment:
        """Create one `Post` per variant and publish via the connected
        platform adapter. Records each post's id back on the variant."""
        e = await self.get(org_id, experiment_id)
        e.start()
        platform = await self.platform_repo.get(org_id, e.platform_id)
        if not platform:
            raise ValueError("experiment platform not found")

        adapter_cls = self.registry.get(PluginKind.PLATFORM, platform.plugin_name)
        if not adapter_cls:
            raise ValueError(f"plugin {platform.plugin_name!r} not registered")

        for variant in e.variants:
            try:
                draft = DraftPost(
                    platform_name=platform.plugin_name,
                    text=variant.text,
                    hashtags=[Hashtag(h) for h in variant.hashtags],
                    media=[],
                )
                post = Post.from_draft(
                    org_id=org_id,
                    workflow_id=e.workflow_id,
                    run_id=run_id,
                    platform_id=platform.id,
                    draft=draft,
                )
                post.status = PostStatus.PUBLISHED  # adapter mutates again below
                await self.post_repo.add(post)

                adapter = adapter_cls(platform.credentials)            # type: ignore[arg-type]
                payload = PostPayload(
                    text=draft.text,
                    hashtags=list(draft.hashtags),
                    media=[],
                )
                pub = await adapter.publish(payload)
                post.mark_published(pub.external_post_id)
                await self.post_repo.update(post)
                variant.mark_published(post.id)
            except Exception as exc:                                   # noqa: BLE001
                log.exception(
                    "experiment_variant_publish_failed",
                    experiment_id=str(e.id), variant=variant.label, error=str(exc),
                )
                variant.status = VariantStatus.FAILED
                variant.notes = str(exc)
        return await self.repo.update(e)

    async def collect_metrics(
        self,
        *,
        org_id: OrgId,
        experiment_id: ExperimentId,
    ) -> Experiment:
        """Pull the current metric value from each variant's Post and stash
        it on the variant. Idempotent."""
        e = await self.get(org_id, experiment_id)
        for variant in e.variants:
            if variant.post_id is None:
                continue
            post = await self.post_repo.get(org_id, variant.post_id)
            if not post or not post.metrics:
                continue
            value = _extract_metric(post.metrics, e.metric)
            if value is not None:
                variant.record_metric(value)
        return await self.repo.update(e)

    async def maybe_settle(
        self,
        *,
        org_id: OrgId,
        experiment_id: ExperimentId,
        now: datetime | None = None,
    ) -> Experiment:
        e = await self.get(org_id, experiment_id)
        if not e.is_settling_complete(now or datetime.utcnow()):
            return e
        e.settle()
        return await self.repo.update(e)

    async def settle_all_running(self) -> list[ExperimentId]:
        """Used by a periodic worker — settle every running experiment whose
        window has elapsed."""
        settled: list[ExperimentId] = []
        for e in await self.repo.list_running():
            if e.is_settling_complete(datetime.utcnow()):
                e.settle()
                await self.repo.update(e)
                settled.append(e.id)
        return settled


# ── helpers ────────────────────────────────────────────────────────────────
def _normalize_weights(
    variants: list[Variant], allocation: AllocationKind,
) -> list[Variant]:
    if not variants:
        return []
    n = len(variants)
    if allocation is AllocationKind.HOLDOUT:
        # First variant is "control" (10%), rest split evenly.
        out = []
        for i, v in enumerate(variants):
            v.weight = 0.1 if i == 0 else 0.9 / max(1, n - 1)
            out.append(v)
        return out
    # EQUAL or BANDIT (initial weights — bandit will adapt later).
    for v in variants:
        v.weight = 1.0 / n
    return variants


def _extract_metric(metrics: dict, key: str) -> float | None:
    """Tolerate the variability of platform metric payloads. We try the
    exact key first, then a small set of aliases."""
    if not metrics:
        return None
    if key in metrics and isinstance(metrics[key], (int, float)):
        return float(metrics[key])
    aliases: dict[str, Iterable[str]] = {
        "engagement_rate": ("engagement", "engagement_pct", "interaction_rate"),
        "likes": ("favorites", "reactions"),
        "shares": ("retweets", "reposts"),
        "comments": ("replies",),
        "impressions": ("views", "reach"),
    }
    for alt in aliases.get(key, ()):
        if alt in metrics and isinstance(metrics[alt], (int, float)):
            return float(metrics[alt])
    return None
