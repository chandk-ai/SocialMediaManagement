"""In-memory SourceItemsService — same surface as the Postgres one.

Used in dev / tests / the memory-persistence backend. Identical method
signatures so workflow_service doesn't need to know which implementation
it has. Loses data on process restart (intentional — this is the
ephemeral path).

Concurrency: all reads/writes go through an asyncio.Lock so the atomic
``mark_consumed`` semantics hold even under contention. We don't pretend
to have row-level Postgres semantics — single-process only.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable
from uuid import uuid4

from app.domain.entities.source import SourceItem
from app.domain.value_objects.ids import OrgId, PostId, RunId, SourceId

from .source_items import SeenItem


@dataclass
class _Row:
    id: str
    org_id: str
    source_id: str
    external_id: str
    title: str = ""
    body_hash: str | None = None
    url: str | None = None
    metadata: dict = field(default_factory=dict)
    status: str = "new"
    skipped_reason: str | None = None
    consumed_by_post_id: str | None = None
    consumed_by_run_id: str | None = None
    consumed_at: datetime | None = None
    first_seen_at: datetime = field(default_factory=datetime.utcnow)
    last_seen_at: datetime = field(default_factory=datetime.utcnow)
    item_published_at: datetime | None = None
    tags: list[str] = field(default_factory=list)


class InMemorySourceItemsService:
    """Same shape as ``SourceItemsService`` (Postgres). Single-process safe
    via an asyncio.Lock — fine for dev and tests, not multi-pod prod."""

    def __init__(self) -> None:
        # (source_id_str, external_id) → _Row
        self._rows: dict[tuple[str, str], _Row] = {}
        # source_id_str → list[_Row]   (denormalised for fast list queries)
        self._by_source: dict[str, list[_Row]] = defaultdict(list)
        self._lock = asyncio.Lock()

    # ── on fetch ─────────────────────────────────────────────────────────
    async def upsert_seen(
        self,
        org_id: OrgId,
        source_id: SourceId,
        items: Iterable[SourceItem],
    ) -> list[SeenItem]:
        out: list[SeenItem] = []
        async with self._lock:
            sid = str(source_id)
            now = datetime.utcnow()
            for it in items:
                if not it.external_id:
                    continue
                key = (sid, it.external_id)
                body_hash = _hash_body(it.body)
                existing = self._rows.get(key)
                if existing is None:
                    row = _Row(
                        id=str(uuid4()),
                        org_id=str(org_id),
                        source_id=sid,
                        external_id=it.external_id,
                        title=it.title or "",
                        body_hash=body_hash,
                        url=it.url,
                        metadata=_safe_metadata(it.metadata),
                        first_seen_at=now,
                        last_seen_at=now,
                        item_published_at=it.published_at,
                    )
                    self._rows[key] = row
                    self._by_source[sid].append(row)
                else:
                    # Refresh — preserve status; everything else gets the
                    # latest values from the source plugin.
                    existing.title = it.title or ""
                    existing.body_hash = body_hash
                    existing.url = it.url
                    existing.metadata = _safe_metadata(it.metadata)
                    existing.last_seen_at = now
                    existing.item_published_at = it.published_at
                    row = existing
                out.append(SeenItem(
                    id=row.id, source_id=sid,
                    external_id=row.external_id, title=row.title,
                    status=row.status, body_hash=row.body_hash or body_hash,
                ))
        return out

    # ── selection-time reads ─────────────────────────────────────────────
    async def consumed_keys(
        self, org_id: OrgId, source_ids: list[SourceId],
    ) -> set[tuple[str, str]]:
        if not source_ids:
            return set()
        org = str(org_id)
        wanted = {str(sid) for sid in source_ids}
        async with self._lock:
            return {
                (r.source_id, r.external_id)
                for r in self._rows.values()
                if r.status == "consumed" and r.org_id == org and r.source_id in wanted
            }

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
        async with self._lock:
            row = self._rows.get((str(source_id), external_id))
            # Atomic: only flip if currently 'new'. Same semantics as the
            # Postgres UPDATE WHERE status='new'.
            if row is None or row.status != "new" or row.org_id != str(org_id):
                return False
            row.status = "consumed"
            row.consumed_by_run_id = str(run_id)
            row.consumed_by_post_id = str(post_id) if post_id else None
            row.consumed_at = datetime.utcnow()
            return True

    async def update_post_link(
        self,
        org_id: OrgId,
        source_id: SourceId,
        external_id: str,
        post_id: PostId,
    ) -> None:
        async with self._lock:
            row = self._rows.get((str(source_id), external_id))
            if row is not None and row.org_id == str(org_id):
                row.consumed_by_post_id = str(post_id)

    async def mark_skipped(
        self,
        org_id: OrgId,
        source_id: SourceId,
        external_id: str,
        reason: str,
    ) -> None:
        async with self._lock:
            row = self._rows.get((str(source_id), external_id))
            if row is None or row.org_id != str(org_id):
                return
            if row.status not in ("new", "skipped"):
                return
            row.status = "skipped"
            row.skipped_reason = (reason or "")[:1000]

    async def release_unclaimed_for_run(
        self,
        org_id: OrgId,
        run_id: RunId,
        *,
        post_status_lookup: dict | None = None,
    ) -> int:
        """Adaptive-consumption hook — release tentative claims when a
        run terminates without producing a live post. See the Postgres
        impl docstring for full rationale.

        ``post_status_lookup`` is an optional ``{post_id: status}`` map
        that the in-memory backend uses to mirror the Postgres impl's
        join semantics (which can read posts.status inline via SQL).
        When None, falls back to the simple
        ``consumed_by_post_id IS NULL`` check — preserves test-suite
        behavior where posts aren't materialized.
        """
        released = 0
        async with self._lock:
            for r in self._rows.values():
                if not (
                    r.org_id == str(org_id)
                    and r.consumed_by_run_id == str(run_id)
                ):
                    continue
                if r.consumed_by_post_id is None:
                    should_release = True
                elif post_status_lookup is not None:
                    status = post_status_lookup.get(r.consumed_by_post_id)
                    # No matching post = treat as released; failed = released.
                    should_release = status is None or status == "failed"
                else:
                    should_release = False
                if should_release:
                    r.status = "new"
                    r.consumed_at = None
                    r.consumed_by_run_id = None
                    r.consumed_by_post_id = None
                    r.skipped_reason = None
                    released += 1
        return released

    # ── reads + tags ─────────────────────────────────────────────────────
    async def list_for_source(
        self, org_id: OrgId, source_id: SourceId,
        *, limit: int = 200,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            rows = sorted(
                (
                    r for r in self._by_source.get(str(source_id), [])
                    if r.org_id == str(org_id)
                ),
                key=lambda r: r.last_seen_at,
                reverse=True,
            )[:limit]
            return [_row_to_dict(r) for r in rows]

    async def set_tags(
        self, org_id: OrgId, item_id: str, tags: list[str],
    ) -> None:
        async with self._lock:
            for r in self._rows.values():
                if r.id == str(item_id) and r.org_id == str(org_id):
                    r.tags = list(tags or [])
                    return

    async def reset_to_new(
        self, org_id: OrgId, item_id: str,
    ) -> None:
        async with self._lock:
            for r in self._rows.values():
                if r.id == str(item_id) and r.org_id == str(org_id):
                    r.status = "new"
                    r.consumed_at = None
                    r.consumed_by_run_id = None
                    r.consumed_by_post_id = None
                    r.skipped_reason = None
                    return


# ── helpers (mirror the Postgres service so callers see identical shape) ─
def _hash_body(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _safe_metadata(meta: Any) -> dict:
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


def _row_to_dict(r: _Row) -> dict[str, Any]:
    return {
        "id": r.id, "external_id": r.external_id, "title": r.title,
        "url": r.url, "status": r.status, "tags": list(r.tags),
        "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
        "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        "item_published_at": (
            r.item_published_at.isoformat() if r.item_published_at else None
        ),
        "consumed_at": r.consumed_at.isoformat() if r.consumed_at else None,
        "consumed_by_post_id": r.consumed_by_post_id,
        "skipped_reason": r.skipped_reason,
    }
