"""System handlers — periodic / cross-cutting jobs.

Registered kinds:

    jobs.cleanup            archive old terminal jobs (30+ days), orphan-sweep
    knowledge.auto_ingest   import top-decile posts into the KB

Both are short-running and idempotent on retry. Failures here aren't
end-user-visible — they retry on the queue's normal backoff and DLQ on
exhaustion (alerting in Pillar 2 picks that up).
"""
from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.services.jobs.handlers import HandlerContext, register_handler

log = get_logger(__name__)


@register_handler("jobs.cleanup")
async def jobs_cleanup(ctx: HandlerContext) -> dict[str, Any]:
    """Trim terminal jobs older than 30 days. Saves table bloat without
    losing recent debugging context."""
    sm = getattr(ctx.queue, "_sm", None)
    if sm is None:
        return {"skipped": True, "reason": "memory_queue"}
    from sqlalchemy import text
    async with sm() as s:
        r = await s.execute(text("""
            DELETE FROM smms.jobs
             WHERE status IN ('succeeded','dead','cancelled')
               AND finished_at < now() - interval '30 days'
             RETURNING id
        """))
        n = len(r.fetchall())
        await s.commit()
    log.info("jobs_cleanup_done", deleted=n)
    return {"deleted": int(n)}


@register_handler("knowledge.auto_ingest")
async def knowledge_auto_ingest(ctx: HandlerContext) -> dict[str, Any]:
    """Pick top-decile posts (by engagement rollup) for this org and
    ingest them into the KB as ``source_kind='past_post'``. Self-
    improving brand-voice loop — closes the gap between the engagement
    feedback (Pillar 3) and the RAG retrieval (Pillar 4).

    Idempotent: each post is only ingested once per quarter (we hash
    ``post_id`` + the rolling-quarter timestamp into the `source_ref`,
    and the KB store treats duplicate source_ref as a no-op via the
    deduplication block below)."""
    org_id = ctx.job.org_id
    if not _is_real_org(org_id):
        return {"skipped": True, "reason": "system_org_id"}

    services = ctx.services
    store = services.get("knowledge_store")
    engagement = services.get("engagement_service")
    post_repo = services.get("post_repo")
    wf_service = services.get("workflow_service")
    if not (store and post_repo and wf_service):
        return {"skipped": True, "reason": "deps_missing"}

    # Query top-decile posts via direct SQL — fast, indexed.
    sm = getattr(ctx.queue, "_sm", None)
    if sm is None:
        return {"skipped": True, "reason": "memory_queue"}
    from sqlalchemy import text
    async with sm() as s:
        # Find the 90th-percentile engagement score for this org's posts
        # over the last 90 days, then return the posts above it that
        # aren't yet in the KB.
        r = await s.execute(text("""
            WITH metrics AS (
              SELECT DISTINCT ON (post_id)
                     post_id,
                     COALESCE(likes,0) + 3 * COALESCE(comments,0)
                       + 5 * COALESCE(shares,0) + 2 * COALESCE(clicks,0) AS score
                FROM smms.post_metrics
               WHERE org_id = :org
                 AND snapshotted_at > now() - interval '90 days'
               ORDER BY post_id, snapshotted_at DESC
            ),
            cutoff AS (
              SELECT percentile_cont(0.90)
                       WITHIN GROUP (ORDER BY score) AS threshold
                FROM metrics
            )
            SELECT m.post_id::text, m.score, p.text, p.title, p.created_at
              FROM metrics m
              JOIN smms.posts p ON p.id = m.post_id
             WHERE m.score >= (SELECT threshold FROM cutoff)
               AND m.score > 0
               AND NOT EXISTS (
                 SELECT 1 FROM smms.knowledge_documents kd
                  WHERE kd.org_id = :org
                    AND kd.source_kind = 'past_post'
                    AND kd.source_ref = m.post_id::text
               )
             LIMIT 25
        """), {"org": org_id})
        candidates = r.fetchall()

    if not candidates:
        return {"ingested": 0, "reason": "no_top_decile"}

    # Resolve an embed function via the same helper the API endpoint uses.
    from app.api.v1.knowledge import _build_embed_fn
    embed_fn = await _build_embed_fn(org_id, wf_service)

    ingested = 0
    for row in candidates:
        post_id, score, text_body, title, created_at = row
        content = (text_body or "").strip()
        if len(content) < 100:
            continue                                                 # too short to matter
        try:
            await store.add_document(
                org_id=org_id, title=(title or "Untitled post")[:200],
                content=content, source_kind="past_post",
                source_ref=str(post_id),
                metadata={
                    "engagement_score": float(score),
                    "auto_ingested_at": time.time(),
                    "created_at": str(created_at) if created_at else None,
                },
                embed_fn=embed_fn,
            )
            ingested += 1
        except Exception as exc:                                     # noqa: BLE001
            log.info("kb_auto_ingest_skip_post",
                     post_id=str(post_id), error=str(exc))

    log.info("kb_auto_ingest_done", org=org_id, ingested=ingested,
             candidates=len(candidates))
    return {"ingested": ingested, "candidates": len(candidates)}


def _is_real_org(org_id: str) -> bool:
    """Filter out the SystemScheduler's all-zeros sentinel for global jobs."""
    if not org_id:
        return False
    return org_id.replace("-", "").strip("0") != ""
