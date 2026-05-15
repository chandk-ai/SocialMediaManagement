"""Source-items persistence service.

Wraps ``smms.source_items`` (migration 009) with the operations the
selection layer + orchestrator actually need:

* ``upsert_seen()``     — called by load_items() with everything a source
                          plugin yielded; refreshes last_seen_at, sets
                          first_seen_at on first encounter.
* ``consumed_keys()``   — set of (source_id, external_id) pairs already in
                          status='consumed'. Selection strategies use this
                          for de-dup.
* ``mark_consumed()``   — atomic claim during the SELECTING step. If
                          another concurrent run already claimed the same
                          item, this is a no-op (we never produce duplicate
                          posts for the same source item).
* ``mark_skipped()``    — recorded skips with reasons (helpful for "why
                          didn't today's blog post go out?").
* ``set_tags()``        — user-applied tags (the API surfaces these so
                          customers can pin / hold / archive items).

Uses the same async_sessionmaker pattern as the other Postgres-backed
services. Falls back to a process-local in-memory implementation when
``PERSISTENCE_BACKEND=memory`` so the in-memory test path keeps working.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.domain.value_objects.ids import OrgId, PostId, RunId, SourceId

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SeenItem:
    """A SourceItem after upsert — carries the persistent ID + status so
    the orchestrator can look up what to claim later."""
    id: str
    source_id: str
    external_id: str
    title: str
    status: str
    body_hash: str


class SourceItemsService:
    """Postgres-backed implementation. Construct via deps factory."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sm = session_factory

    # ── on fetch ─────────────────────────────────────────────────────────
    async def upsert_seen(
        self,
        org_id: OrgId,
        source_id: SourceId,
        items: Iterable[SourceItem],
    ) -> list[SeenItem]:
        """Record every item the source yielded. Idempotent on the
        UNIQUE (source_id, external_id) constraint:

        * First time we see (source, external_id): row inserted with
          status='new', body_hash, first_seen_at=now().
        * Re-seen with the SAME body: only last_seen_at refreshed.
        * Re-seen with a DIFFERENT body: body_hash + metadata refreshed,
          but status is preserved so a previously-consumed item that the
          author edited stays consumed (the selection layer can decide
          whether body_hash drift means "re-process" — out of scope here).
        """
        out: list[SeenItem] = []
        async with self._sm() as s:
            for it in items:
                if not it.external_id:
                    # Source plugin didn't supply a stable id — can't dedup.
                    continue
                body_hash = _hash_body(it.body)
                pub_at = it.published_at
                row = (await s.execute(
                    text("""
                        INSERT INTO smms.source_items
                            (id, org_id, source_id, external_id, title,
                             body_hash, url, metadata, item_published_at,
                             first_seen_at, last_seen_at)
                        VALUES
                            (:id, :org_id, :source_id, :external_id, :title,
                             :body_hash, :url, cast(:metadata as jsonb),
                             :pub_at, now(), now())
                        ON CONFLICT (source_id, external_id) DO UPDATE
                            SET last_seen_at = now(),
                                title = EXCLUDED.title,
                                body_hash = EXCLUDED.body_hash,
                                url = EXCLUDED.url,
                                metadata = EXCLUDED.metadata,
                                item_published_at = EXCLUDED.item_published_at
                        RETURNING id, status, body_hash
                    """),
                    {
                        "id": uuid4(),
                        "org_id": UUID(str(org_id)),
                        "source_id": UUID(str(source_id)),
                        "external_id": it.external_id,
                        "title": it.title or "",
                        "body_hash": body_hash,
                        "url": it.url,
                        "metadata": json.dumps(_safe_metadata(it.metadata)),
                        "pub_at": pub_at,
                    },
                )).first()
                out.append(SeenItem(
                    id=str(row.id), source_id=str(source_id),
                    external_id=it.external_id, title=it.title or "",
                    status=row.status, body_hash=row.body_hash or body_hash,
                ))
            await s.commit()
        return out

    # ── selection-time reads ─────────────────────────────────────────────
    async def consumed_keys(
        self, org_id: OrgId, source_ids: list[SourceId],
    ) -> set[tuple[str, str]]:
        """Return {(source_id, external_id)} already in status='consumed'.
        Selection strategies pass this in the SelectionContext for dedup.

        Scoped to the supplied source_ids only — we don't care about other
        workflows' consumption when this workflow runs."""
        if not source_ids:
            return set()
        async with self._sm() as s:
            rows = (await s.execute(
                text("""
                    SELECT source_id, external_id
                    FROM smms.source_items
                    WHERE org_id = :org_id
                      AND source_id = ANY(:source_ids)
                      AND status = 'consumed'
                """),
                {
                    "org_id": UUID(str(org_id)),
                    "source_ids": [UUID(str(sid)) for sid in source_ids],
                },
            )).fetchall()
        return {(str(r.source_id), r.external_id) for r in rows}

    # ── claim ────────────────────────────────────────────────────────────
    async def mark_consumed(
        self,
        org_id: OrgId,
        source_id: SourceId,
        external_id: str,
        *,
        run_id: RunId,
        post_id: PostId | None = None,
    ) -> bool:
        """Atomic claim: flip status='new' -> 'consumed' if and only if it's
        currently 'new'. Returns True if WE claimed it, False if someone
        else already did (concurrent run) or it doesn't exist.

        post_id can be None at claim-time (we claim during SELECTING, before
        the agent loop), then patched on Post-insert via update_post_link()."""
        async with self._sm() as s:
            res = await s.execute(
                text("""
                    UPDATE smms.source_items
                    SET status = 'consumed',
                        consumed_by_run_id = :run_id,
                        consumed_by_post_id = :post_id,
                        consumed_at = now()
                    WHERE org_id = :org_id
                      AND source_id = :source_id
                      AND external_id = :external_id
                      AND status = 'new'
                """),
                {
                    "org_id": UUID(str(org_id)),
                    "source_id": UUID(str(source_id)),
                    "external_id": external_id,
                    "run_id": UUID(str(run_id)),
                    "post_id": UUID(str(post_id)) if post_id else None,
                },
            )
            await s.commit()
            return res.rowcount > 0

    async def update_post_link(
        self,
        org_id: OrgId,
        source_id: SourceId,
        external_id: str,
        post_id: PostId,
    ) -> None:
        """Once the Post is created, back-fill ``consumed_by_post_id``."""
        async with self._sm() as s:
            await s.execute(
                text("""
                    UPDATE smms.source_items
                    SET consumed_by_post_id = :post_id
                    WHERE org_id = :org_id
                      AND source_id = :source_id
                      AND external_id = :external_id
                """),
                {
                    "org_id": UUID(str(org_id)),
                    "source_id": UUID(str(source_id)),
                    "external_id": external_id,
                    "post_id": UUID(str(post_id)),
                },
            )
            await s.commit()

    async def release_unclaimed_for_run(
        self,
        org_id: OrgId,
        run_id: RunId,
    ) -> int:
        """Release source_items that were tentatively claimed by ``run_id``
        but never produced a live post. Adaptive-consumption hook.

        Why this exists
        ───────────────
        The Selector calls ``mark_consumed`` BEFORE the agent loop runs —
        an eager concurrency lock so two concurrent runs can't pick the
        same item. But if the run subsequently terminates without
        actually publishing anything (reject, expire, cancel, mid-run
        error), the claim becomes a permanent silent skip on every
        future run. The user's content is "stuck" with no signal as to
        why.

        The contract: a source item is permanently consumed IFF a
        non-failed Post derived from it exists in the database. "Non-
        failed" means status in {draft, review, approved, scheduled,
        published} — any of those is a live commitment to publish that
        content. A Post in status='failed' is dead; the source item
        that produced it should be released for re-attempt.

        Note ``consumed_by_post_id`` is set the moment a Post row is
        inserted (REVIEW state), well before publish. So we can't gate
        on ``consumed_by_post_id IS NULL`` alone — we have to join to
        the posts table and check its current status. This way the
        rule is uniform: terminal-without-success → release.

        Idempotent — calling twice is harmless. Returns the number of
        items released so callers can log it.
        """
        async with self._sm() as s:
            res = await s.execute(
                text("""
                    UPDATE smms.source_items AS si
                    SET status = 'new',
                        consumed_at = NULL,
                        consumed_by_run_id = NULL,
                        consumed_by_post_id = NULL,
                        skipped_reason = NULL
                    WHERE si.org_id = :org_id
                      AND si.consumed_by_run_id = :run_id
                      AND (
                          si.consumed_by_post_id IS NULL
                          OR NOT EXISTS (
                              SELECT 1
                              FROM smms.posts AS p
                              WHERE p.id = si.consumed_by_post_id
                                AND p.status <> 'failed'
                          )
                      )
                """),
                {
                    "org_id": UUID(str(org_id)),
                    "run_id": UUID(str(run_id)),
                },
            )
            await s.commit()
            return int(res.rowcount or 0)

    async def mark_skipped(
        self,
        org_id: OrgId,
        source_id: SourceId,
        external_id: str,
        reason: str,
    ) -> None:
        """Record a skip with a reason. Never overwrites a 'consumed' row —
        a previously-used item that this run skipped doesn't go backwards
        in status."""
        async with self._sm() as s:
            await s.execute(
                text("""
                    UPDATE smms.source_items
                    SET status = 'skipped', skipped_reason = :reason
                    WHERE org_id = :org_id
                      AND source_id = :source_id
                      AND external_id = :external_id
                      AND status IN ('new', 'skipped')
                """),
                {
                    "org_id": UUID(str(org_id)),
                    "source_id": UUID(str(source_id)),
                    "external_id": external_id,
                    "reason": reason[:1000],
                },
            )
            await s.commit()

    # ── user-applied tags + reads ────────────────────────────────────────
    async def list_for_source(
        self, org_id: OrgId, source_id: SourceId,
        *, limit: int = 200,
    ) -> list[dict[str, Any]]:
        async with self._sm() as s:
            rows = (await s.execute(
                text("""
                    SELECT id, external_id, title, url, status, tags,
                           first_seen_at, last_seen_at, item_published_at,
                           consumed_at, consumed_by_post_id, skipped_reason
                    FROM smms.source_items
                    WHERE org_id = :org_id AND source_id = :source_id
                    ORDER BY last_seen_at DESC
                    LIMIT :limit
                """),
                {
                    "org_id": UUID(str(org_id)),
                    "source_id": UUID(str(source_id)),
                    "limit": int(limit),
                },
            )).fetchall()
        return [
            {
                "id": str(r.id), "external_id": r.external_id, "title": r.title,
                "url": r.url, "status": r.status, "tags": list(r.tags or []),
                "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
                "item_published_at": (
                    r.item_published_at.isoformat() if r.item_published_at else None
                ),
                "consumed_at": r.consumed_at.isoformat() if r.consumed_at else None,
                "consumed_by_post_id": (
                    str(r.consumed_by_post_id) if r.consumed_by_post_id else None
                ),
                "skipped_reason": r.skipped_reason,
            }
            for r in rows
        ]

    async def set_tags(
        self, org_id: OrgId, item_id: str, tags: list[str],
    ) -> None:
        async with self._sm() as s:
            await s.execute(
                text("""
                    UPDATE smms.source_items
                    SET tags = :tags
                    WHERE org_id = :org_id AND id = :id
                """),
                {
                    "org_id": UUID(str(org_id)),
                    "id": UUID(str(item_id)),
                    "tags": list(tags or []),
                },
            )
            await s.commit()

    async def reset_to_new(
        self, org_id: OrgId, item_id: str,
    ) -> None:
        """Operator escape hatch — un-consume / un-skip an item so a future
        run can re-process it. Useful when a workflow misfires and you
        want to retry without editing the source."""
        async with self._sm() as s:
            await s.execute(
                text("""
                    UPDATE smms.source_items
                    SET status = 'new',
                        consumed_at = NULL,
                        consumed_by_run_id = NULL,
                        consumed_by_post_id = NULL,
                        skipped_reason = NULL
                    WHERE org_id = :org_id AND id = :id
                """),
                {"org_id": UUID(str(org_id)), "id": UUID(str(item_id))},
            )
            await s.commit()


# ── helpers ────────────────────────────────────────────────────────────────
def _hash_body(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _safe_metadata(meta: Any) -> dict:
    """Strip any non-JSON-serialisable cruft so the JSONB cast doesn't choke."""
    if not isinstance(meta, dict):
        return {}
    out: dict = {}
    for k, v in meta.items():
        if isinstance(v, (str, int, float, bool, list, dict)) or v is None:
            out[str(k)] = v
        elif isinstance(v, datetime):
            out[str(k)] = v.isoformat()
        else:
            out[str(k)] = str(v)
    return out
