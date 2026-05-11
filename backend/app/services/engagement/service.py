"""EngagementService — pulls metrics, persists snapshots, attributes.

Wired in two flavours:

* Postgres-backed: snapshots table + rollup table for fast reads.
* In-memory: same surface, single-process; rollups recomputed on
  every call.

Hot paths:

* ``fetch_for_post(org_id, post_id)``
    Called by the engagement.fetch worker job (T+1h, T+24h after
    publish, plus on-demand). Looks up the post + platform, asks the
    adapter for current metrics via its ``get_metrics()`` method,
    persists the snapshot, returns it.

* ``aggregate(org_id)``
    Recomputes the rollup table for every dimension we care about.
    Cheap on the in-memory backend, indexed enough on Postgres that
    it can run every 5–10 min via the engagement.aggregate worker.

* ``learnings(org_id, *, dim)``
    Read path for the analytics page and the learning loop. Returns
    AttributionResult rows.

Pillar 3 also exposes ``learnings_for_selection()`` which the
selection layer can call to bias toward sources / strategies that
historically produced more engagement.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.core.metrics import M
from app.services.engagement.attribution import (
    AttributionDimension, AttributionResult, attribute, engagement_score,
)

log = get_logger(__name__)


class EngagementService(ABC):
    @abstractmethod
    async def fetch_for_post(
        self, *, org_id: str, post_id: str,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def aggregate(self, *, org_id: str) -> int: ...

    @abstractmethod
    async def list_recent(
        self, org_id: str, *, limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def learnings(
        self, org_id: str, *, dim: AttributionDimension,
        limit: int = 50,
    ) -> list[AttributionResult]: ...

    async def learnings_for_selection(
        self, org_id: str, *, source_ids: list[str] | None = None,
    ) -> dict[str, float]:
        """Returns ``{source_id: avg_engagement}`` for the requested
        sources (or all sources if None). Selection strategies use this
        to bias item ranking toward historically-performing sources."""
        rows = await self.learnings(org_id, dim=AttributionDimension.SOURCE,
                                     limit=200)
        out = {r.key: r.avg_engagement for r in rows}
        if source_ids:
            out = {k: v for k, v in out.items() if k in set(source_ids)}
        return out


# ── memory ─────────────────────────────────────────────────────────────
class InMemoryEngagementService(EngagementService):
    """Single-process implementation. Stores snapshots in a list,
    recomputes attribution on every call."""

    def __init__(self, *, post_repo=None, platform_repo=None,
                 registry=None) -> None:
        self.post_repo = post_repo
        self.platform_repo = platform_repo
        self.registry = registry
        self._snapshots: list[dict[str, Any]] = []

    async def fetch_for_post(self, *, org_id: str, post_id: str):
        snapshot = await _adapter_fetch(
            org_id=org_id, post_id=post_id,
            post_repo=self.post_repo, platform_repo=self.platform_repo,
            registry=self.registry,
        )
        if snapshot is None:
            M.engagement_fetch.labels(platform="unknown",
                                       status="error").inc()
            return {"skipped": True}
        snapshot["org_id"] = org_id
        snapshot["post_id"] = post_id
        snapshot["snapshotted_at"] = datetime.now(timezone.utc)
        self._snapshots.append(snapshot)
        M.engagement_fetch.labels(
            platform=snapshot.get("platform_kind", "unknown"),
            status="ok").inc()
        return snapshot

    async def aggregate(self, *, org_id: str) -> int:
        # Memory backend's aggregation is on-demand inside learnings()
        return len([s for s in self._snapshots if s.get("org_id") == org_id])

    async def list_recent(self, org_id, *, limit=100):
        rows = [s for s in self._snapshots if s.get("org_id") == org_id]
        rows.sort(key=lambda s: s.get("snapshotted_at") or datetime.min,
                   reverse=True)
        return rows[:limit]

    async def learnings(self, org_id, *, dim: AttributionDimension,
                         limit: int = 50):
        rows = [s for s in self._snapshots if s.get("org_id") == org_id]
        if not rows:
            return []
        # Pair each snapshot with synthetic post data already embedded.
        pairs = [(s, s) for s in rows]
        out = attribute(pairs, dimension=dim)
        return out[:limit]


# ── postgres ───────────────────────────────────────────────────────────
class PostgresEngagementService(EngagementService):
    def __init__(
        self, session_maker, *, post_repo=None,
        platform_repo=None, registry=None,
    ) -> None:
        self._sm = session_maker
        self.post_repo = post_repo
        self.platform_repo = platform_repo
        self.registry = registry

    async def fetch_for_post(self, *, org_id: str, post_id: str):
        snapshot = await _adapter_fetch(
            org_id=org_id, post_id=post_id,
            post_repo=self.post_repo, platform_repo=self.platform_repo,
            registry=self.registry,
        )
        if snapshot is None:
            M.engagement_fetch.labels(platform="unknown",
                                       status="error").inc()
            return {"skipped": True}

        from sqlalchemy import text
        async with self._sm() as s:
            await s.execute(text("""
                INSERT INTO smms.post_metrics
                  (org_id, post_id, snapshotted_at, likes, comments,
                   shares, impressions, reach, clicks, saves, plays,
                   watch_time_s, extra, fetch_error, fetch_source)
                VALUES
                  (:org, :post, now(), :likes, :comments,
                   :shares, :impressions, :reach, :clicks, :saves,
                   :plays, :wts, CAST(:extra AS JSONB), :err, :src)
            """), {
                "org": org_id, "post": post_id,
                "likes": snapshot.get("likes"),
                "comments": snapshot.get("comments"),
                "shares": snapshot.get("shares"),
                "impressions": snapshot.get("impressions"),
                "reach": snapshot.get("reach"),
                "clicks": snapshot.get("clicks"),
                "saves": snapshot.get("saves"),
                "plays": snapshot.get("plays"),
                "wts": snapshot.get("watch_time_s"),
                "extra": _json(snapshot.get("extra") or {}),
                "err": snapshot.get("fetch_error"),
                "src": snapshot.get("fetch_source", "auto"),
            })
            await s.commit()
        M.engagement_fetch.labels(
            platform=snapshot.get("platform_kind", "unknown"),
            status="ok").inc()
        return snapshot

    async def aggregate(self, *, org_id: str) -> int:
        """Recompute rollup table. Walks the latest snapshot per post,
        groups by every dimension, persists. Returns row count."""
        from sqlalchemy import text
        # Wipe the rollup so we don't accumulate stale rows. Cheap
        # because it's per-org.
        async with self._sm() as s:
            await s.execute(text("""
                DELETE FROM smms.post_metrics_rollup WHERE org_id=:org
            """), {"org": org_id})

            # Latest snapshot per post + post metadata.
            r = await s.execute(text("""
                WITH latest AS (
                  SELECT DISTINCT ON (post_id) post_id, snapshotted_at,
                    likes, comments, shares, impressions, reach, clicks,
                    saves, plays, watch_time_s
                    FROM smms.post_metrics
                   WHERE org_id=:org
                   ORDER BY post_id, snapshotted_at DESC
                )
                SELECT p.id::text, p.workflow_id::text, p.run_id::text,
                       p.platform_id::text,
                       pl.plugin_name AS platform_kind,
                       p.published_at,
                       latest.likes, latest.comments, latest.shares,
                       latest.impressions, latest.reach, latest.clicks,
                       latest.saves, latest.plays, latest.watch_time_s
                  FROM latest
                  JOIN smms.posts p ON p.id = latest.post_id
             LEFT JOIN smms.platforms pl ON pl.id = p.platform_id
                 WHERE p.org_id=:org
            """), {"org": org_id})
            rows = r.fetchall()

        # Compute aggregates in Python — small N per org. ``Post`` has
        # no metadata field on the entity, so we don't carry one here.
        post_dicts = []
        for row in rows:
            post_dicts.append({
                "id": row[0], "workflow_id": row[1], "run_id": row[2],
                "platform_id": row[3], "platform_kind": row[4],
                "published_at": row[5],
                "likes": row[6], "comments": row[7], "shares": row[8],
                "impressions": row[9], "reach": row[10], "clicks": row[11],
                "saves": row[12], "plays": row[13],
                "watch_time_s": row[14],
            })

        rollup_rows: list[dict[str, Any]] = []
        for dim in AttributionDimension:
            results = attribute(
                [(p, p) for p in post_dicts], dimension=dim,
            )
            for r in results:
                row = {
                    "org_id": org_id,
                    "workflow_id": None, "source_id": None,
                    "strategy": None, "platform_kind": None,
                    "utc_hour": None, "weekday": None,
                    "sample_size": r.sample_size,
                    "avg_engagement": r.avg_engagement,
                    "p50_engagement": r.p50_engagement,
                    "p90_engagement": r.p90_engagement,
                }
                if dim is AttributionDimension.SOURCE:
                    row["source_id"] = _safe_uuid(r.key)
                elif dim is AttributionDimension.WORKFLOW:
                    row["workflow_id"] = _safe_uuid(r.key)
                elif dim is AttributionDimension.STRATEGY:
                    row["strategy"] = r.key
                elif dim is AttributionDimension.PLATFORM:
                    row["platform_kind"] = r.key
                elif dim is AttributionDimension.HOUR:
                    try:
                        row["utc_hour"] = int(r.key.lstrip("h"))
                    except Exception:                                # noqa: BLE001
                        continue
                elif dim is AttributionDimension.WEEKDAY:
                    wd = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                    if r.key in wd:
                        row["weekday"] = wd.index(r.key)
                rollup_rows.append(row)

        if not rollup_rows:
            return 0

        async with self._sm() as s:
            from sqlalchemy import text
            for row in rollup_rows:
                await s.execute(text("""
                    INSERT INTO smms.post_metrics_rollup
                      (org_id, workflow_id, source_id, strategy,
                       platform_kind, utc_hour, weekday,
                       sample_size, avg_engagement, p50_engagement, p90_engagement)
                    VALUES
                      (:org, :wf, :sid, :strategy, :platform_kind,
                       :hour, :weekday, :n, :avg, :p50, :p90)
                """), {
                    "org": row["org_id"],
                    "wf": row["workflow_id"], "sid": row["source_id"],
                    "strategy": row["strategy"],
                    "platform_kind": row["platform_kind"],
                    "hour": row["utc_hour"], "weekday": row["weekday"],
                    "n": row["sample_size"],
                    "avg": row["avg_engagement"],
                    "p50": row["p50_engagement"],
                    "p90": row["p90_engagement"],
                })
            await s.commit()
        return len(rollup_rows)

    async def list_recent(self, org_id, *, limit=100):
        from sqlalchemy import text
        async with self._sm() as s:
            r = await s.execute(text("""
                SELECT id::text, post_id::text, snapshotted_at,
                       likes, comments, shares, impressions, reach,
                       clicks, saves, plays, watch_time_s, extra,
                       fetch_error
                  FROM smms.post_metrics
                 WHERE org_id=:org
                 ORDER BY snapshotted_at DESC LIMIT :lim
            """), {"org": org_id, "lim": int(limit)})
            return [{
                "id": row[0], "post_id": row[1],
                "snapshotted_at": row[2].isoformat() if row[2] else None,
                "likes": row[3], "comments": row[4], "shares": row[5],
                "impressions": row[6], "reach": row[7],
                "clicks": row[8], "saves": row[9], "plays": row[10],
                "watch_time_s": row[11], "extra": row[12],
                "fetch_error": row[13],
            } for row in r.fetchall()]

    async def learnings(self, org_id, *, dim: AttributionDimension,
                         limit: int = 50):
        from sqlalchemy import text
        async with self._sm() as s:
            clauses = ["org_id=:org"]
            params: dict[str, Any] = {"org": org_id, "lim": int(limit)}
            if dim is AttributionDimension.SOURCE:
                clauses.append("source_id IS NOT NULL")
            elif dim is AttributionDimension.WORKFLOW:
                clauses.append("workflow_id IS NOT NULL")
            elif dim is AttributionDimension.STRATEGY:
                clauses.append("strategy IS NOT NULL")
            elif dim is AttributionDimension.PLATFORM:
                clauses.append("platform_kind IS NOT NULL")
            elif dim is AttributionDimension.HOUR:
                clauses.append("utc_hour IS NOT NULL")
            elif dim is AttributionDimension.WEEKDAY:
                clauses.append("weekday IS NOT NULL")
            r = await s.execute(text(f"""
                SELECT
                    CASE
                      WHEN :dim = 'source'   THEN source_id::text
                      WHEN :dim = 'workflow' THEN workflow_id::text
                      WHEN :dim = 'strategy' THEN strategy
                      WHEN :dim = 'platform' THEN platform_kind
                      WHEN :dim = 'hour'     THEN 'h' || lpad(utc_hour::text, 2, '0')
                      WHEN :dim = 'weekday'  THEN
                        (ARRAY['mon','tue','wed','thu','fri','sat','sun'])[weekday + 1]
                    END AS key,
                    avg_engagement, p50_engagement, p90_engagement, sample_size
                FROM smms.post_metrics_rollup
                WHERE {' AND '.join(clauses)}
                ORDER BY avg_engagement DESC LIMIT :lim
            """), {**params, "dim": dim.value})
            return [AttributionResult(
                key=row[0], avg_engagement=float(row[1] or 0),
                p50_engagement=float(row[2] or 0),
                p90_engagement=float(row[3] or 0),
                sample_size=int(row[4] or 0),
            ) for row in r.fetchall() if row[0]]


# ── adapter glue ───────────────────────────────────────────────────────
async def _adapter_fetch(
    *, org_id, post_id, post_repo, platform_repo, registry,
) -> dict[str, Any] | None:
    """Resolve the platform adapter for this post and ask it for current
    engagement. Returns None when we can't resolve the post or the
    platform doesn't support metrics yet."""
    if post_repo is None or platform_repo is None or registry is None:
        return None
    try:
        # Repos are org-scoped — every .get() takes (org_id, entity_id).
        # Coerce the string org_id to the typed OrgId once.
        from app.domain.value_objects.ids import OrgId, PostId
        from uuid import UUID
        org_typed = OrgId(UUID(org_id) if isinstance(org_id, str) else org_id)

        post = await post_repo.get(org_typed, PostId(post_id))
        if post is None:
            return None
        platform = await platform_repo.get(org_typed, post.platform_id)
        if platform is None:
            return None
        # Platform.plugin_name is the registry key, NOT a ``.kind``
        # attribute. We keep the ``platform_kind`` JSON key in the
        # snapshot because that's what the attribution + rollup tables
        # use — internal column name, not a domain concept.
        from app.plugins.registry import PluginKind
        plugin_name = platform.plugin_name
        try:
            entry = registry.get(PluginKind.PLATFORM, plugin_name)
        except Exception:                                            # noqa: BLE001
            return {"platform_kind": plugin_name,
                    "fetch_error": f"no adapter registered for {plugin_name}"}
        adapter_cls = entry.cls
        # Pass the OAuth credentials we already stored on the Platform
        # entity. Adapters need this for any authenticated read; an
        # unauthenticated adapter would 401 on every metrics fetch.
        adapter = adapter_cls(
            credentials=getattr(platform, "credentials", None),
            config=getattr(platform, "config", None) or {},
        )
        ext_id = post.external_post_id
        if not ext_id:
            return {"platform_kind": plugin_name,
                    "fetch_error": "no external_post_id"}
        snap = await adapter.fetch_metrics(ext_id)
        snap = dict(snap or {})
        snap.setdefault("platform_kind", plugin_name)
        return snap
    except Exception as exc:                                          # noqa: BLE001
        log.warning("engagement_adapter_fetch_failed",
                    post_id=str(post_id), error=str(exc))
        return {"fetch_error": str(exc)[:200]}


def _json(v):
    import json
    return json.dumps(v)


def _safe_uuid(s: str | None):
    """Returns a UUID string if s parses, else None — for nullable
    UUID columns where the dim key is something like 'tuesday'."""
    if not s:
        return None
    try:
        from uuid import UUID
        return str(UUID(str(s)))
    except Exception:                                                 # noqa: BLE001
        return None
