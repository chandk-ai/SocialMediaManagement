"""Append-only audit log service.

Writes one row per state-changing operation to ``smms.audit_log``. Designed
to be **fire-and-forget** — every write swallows exceptions and logs them.
A failed audit insert must never break a user-visible action.

Schema reminder (see migrations/001_init.sql):

    id, org_id, actor_type, actor_id, action, resource_type, resource_id,
    before, after, ip, user_agent, request_id, occurred_at

Convention for ``action``: lower-case, dot-separated, ``noun.verb``.
Examples: ``llm_key.set``, ``platform.connect``, ``workflow.activate``,
``post.publish``, ``post.fail``.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.logging import get_logger, request_id_var
from app.domain.value_objects.ids import OrgId

log = get_logger(__name__)


class AuditLogService:
    """Tiny service that owns the append path and a paged read."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sm = session_factory

    # ── writes ──────────────────────────────────────────────────────────
    async def record(
        self,
        *,
        org_id: OrgId | str,
        action: str,
        resource_type: str,
        resource_id: str | UUID | None = None,
        actor_type: str = "user",
        actor_id: str | UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        rid = request_id_var.get()
        try:
            async with self._sm() as s:
                await s.execute(
                    text(
                        "INSERT INTO smms.audit_log "
                        "(org_id, actor_type, actor_id, action, resource_type, "
                        " resource_id, before, after, ip, user_agent, request_id) "
                        "VALUES (:org_id, :atype, :aid, :action, :rtype, :rid, "
                        "        cast(:before as jsonb), cast(:after as jsonb), "
                        "        cast(:ip as inet), :ua, :req_id)"
                    ),
                    {
                        "org_id": UUID(str(org_id)),
                        "atype": actor_type,
                        "aid": UUID(str(actor_id)) if actor_id else None,
                        "action": action,
                        "rtype": resource_type,
                        "rid": UUID(str(resource_id)) if resource_id else None,
                        "before": json.dumps(before) if before is not None else None,
                        "after": json.dumps(after) if after is not None else None,
                        "ip": ip,
                        "ua": (user_agent or "")[:500] or None,
                        "req_id": rid,
                    },
                )
                await s.commit()
        except Exception as exc:                                        # noqa: BLE001
            # Never propagate — audit must not break user flows.
            log.warning(
                "audit_log_write_failed",
                action=action,
                resource_type=resource_type,
                error=str(exc),
            )

    # ── reads ───────────────────────────────────────────────────────────
    async def verify_chain(
        self, org_id: OrgId | str, *, limit: int = 5000,
    ) -> dict[str, Any]:
        """Walk the org's append-only audit chain and confirm each row's
        ``row_hash`` matches the recomputed value from the same canonical
        inputs. Returns a summary suitable for compliance review.

        ``smms.audit_log_verified`` (defined in migration 008) does the
        recompute in SQL using pgcrypto's digest(), so this method is a
        thin reporter — it doesn't reimplement the hash in Python (which
        would be a second source of truth that could disagree)."""
        rows: list[dict[str, Any]] = []
        ok_count = 0
        bad: list[dict[str, Any]] = []
        prev_link_ok = True
        prior_hash: str | None = None
        async with self._sm() as s:
            result = await s.execute(
                text(
                    "SELECT id, occurred_at, action, prev_hash, row_hash, "
                    "       expected_row_hash "
                    "FROM smms.audit_log_verified "
                    "WHERE org_id = :org_id "
                    "ORDER BY occurred_at, id "
                    "LIMIT :limit"
                ),
                {"org_id": UUID(str(org_id)), "limit": int(limit)},
            )
            rows = list(result.mappings())

        skipped_legacy = 0
        for r in rows:
            row_hash = r["row_hash"]
            expected = r["expected_row_hash"]
            # Rows inserted before the hash-chain feature shipped
            # (migration 008 / niche #9) have NULL row_hash. They
            # predate verification and should NOT be flagged as
            # "tampered" — they're just untouched legacy entries.
            # Skip them entirely so the banner only fires on REAL
            # post-chain tampering.
            if row_hash is None:
                skipped_legacy += 1
                continue
            row_ok = row_hash == expected
            link_ok = (
                prior_hash is None
                or (r["prev_hash"] or "") == prior_hash
            )
            if row_ok and link_ok:
                ok_count += 1
            else:
                bad.append({
                    "id": str(r["id"]),
                    "occurred_at": r["occurred_at"].isoformat() if r["occurred_at"] else "",
                    "action": r["action"],
                    "row_hash_ok": row_ok,
                    "link_ok": link_ok,
                })
                prev_link_ok = False
            prior_hash = row_hash

        return {
            "total": len(rows),
            "ok": ok_count,
            "tampered": len(bad),
            "chain_intact": prev_link_ok and not bad,
            "first_break": bad[0] if bad else None,
            # Cap the breaks list so a chain that's gone wrong everywhere
            # doesn't return megabytes of JSON.
            "breaks": bad[:25],
        }

    async def list_paged(
        self,
        org_id: OrgId | str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        actor_id: str | UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        """Cursor-paged audit log read.

        Append-only logs are the textbook keyset-pagination case:
        offset pagination would skip / duplicate rows whenever a new
        audit row landed mid-walk. The cursor encodes ``(occurred_at,
        id)`` of the LAST row returned; the next call asks for rows
        strictly older than that tuple, breaking ties on ``id`` so the
        order is total.

        The cursor format is opaque to the client: base64(json) so we
        can extend it later without bumping the API. If a stale cursor
        comes in (data deleted, format mismatch) we treat it as a fresh
        scan rather than 400ing — it's a read API, the worst case is
        the user sees the most-recent page again.

        Filters:
          actor_id      uuid — only events emitted by this user
          action        substring match (ILIKE %x%), case-insensitive
          resource_type exact match
          resource_id   uuid — narrows to one entity's history
          since/until   half-open [since, until) on occurred_at
        Cap: ``limit`` clamped to [1, 100]. The page count keeps the
        wire payload modest even when ``before`` / ``after`` JSON blobs
        are large.

        Returns:
          {
            "events":         [...],
            "next_cursor":    str | None,   # null when no more rows
            "has_more":       bool,
            "total_fetched":  int,
          }
        """
        limit = max(1, min(int(limit), 100))
        # We fetch limit+1 to cheaply detect "is there more after this
        # page" without a separate COUNT round-trip.
        fetch_n = limit + 1

        clauses = ["org_id = :org_id"]
        params: dict[str, Any] = {
            "org_id": UUID(str(org_id)), "limit": fetch_n,
        }

        if cursor:
            decoded = _decode_cursor(cursor)
            if decoded is not None:
                # Keyset predicate: strict less-than on the tuple so the
                # same row never appears on two consecutive pages.
                clauses.append(
                    "(occurred_at, id) < (:cur_ts, :cur_id)"
                )
                params["cur_ts"] = decoded["ts"]
                params["cur_id"] = decoded["id"]

        if actor_id:
            try:
                params["aid"] = UUID(str(actor_id))
                clauses.append("actor_id = :aid")
            except ValueError:
                # Bad UUID — ignore filter rather than 400; the UI sends
                # filters from a typeahead that can momentarily be junk.
                pass
        if action:
            # Case-insensitive substring — easier for operators who
            # remember "publish" but not whether it was post.publish or
            # workflow.publish.
            clauses.append("action ILIKE :action")
            params["action"] = f"%{action.strip()}%"
        if resource_type:
            clauses.append("resource_type = :rtype")
            params["rtype"] = resource_type
        if resource_id:
            try:
                params["rid"] = UUID(str(resource_id))
                clauses.append("resource_id = :rid")
            except ValueError:
                pass
        if since is not None:
            clauses.append("occurred_at >= :since")
            params["since"] = since
        if until is not None:
            clauses.append("occurred_at <  :until")
            params["until"] = until

        sql = (
            "SELECT id, org_id, actor_type, actor_id, action, "
            "       resource_type, resource_id, before, after, "
            "       request_id, occurred_at "
            "FROM smms.audit_log "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY occurred_at DESC, id DESC "
            "LIMIT :limit"
        )

        async with self._sm() as s:
            try:
                result = await s.execute(text(sql), params)
                rows = result.fetchall()
            except SQLAlchemyError as exc:
                log.warning("audit_list_paged_failed", error=str(exc))
                return {
                    "events": [], "next_cursor": None,
                    "has_more": False, "total_fetched": 0,
                }

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor: str | None = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(last.occurred_at, last.id)

        events: list[dict[str, Any]] = []
        for r in page_rows:
            before = r.before if isinstance(r.before, dict) else (
                json.loads(r.before) if r.before else None
            )
            after = r.after if isinstance(r.after, dict) else (
                json.loads(r.after) if r.after else None
            )
            events.append({
                "id": str(r.id),
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else "",
                "actor_type": r.actor_type,
                "actor_id": str(r.actor_id) if r.actor_id else None,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": str(r.resource_id) if r.resource_id else None,
                "before": before,
                "after": after,
                "request_id": r.request_id,
            })

        return {
            "events": events,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "total_fetched": len(events),
        }

    async def distinct_filter_values(
        self, org_id: OrgId | str, *, since: datetime | None = None,
    ) -> dict[str, list[str]]:
        """Populate the filter dropdowns. Returns the distinct
        ``action`` + ``resource_type`` + ``actor_id`` values an org has
        produced in the recent window — small N, cheap query."""
        params: dict[str, Any] = {"org_id": UUID(str(org_id))}
        since_clause = ""
        if since is not None:
            since_clause = " AND occurred_at >= :since"
            params["since"] = since
        async with self._sm() as s:
            actions = (await s.execute(text(
                f"SELECT DISTINCT action FROM smms.audit_log "
                f"WHERE org_id=:org_id{since_clause} "
                f"ORDER BY action LIMIT 200"
            ), params)).fetchall()
            rtypes = (await s.execute(text(
                f"SELECT DISTINCT resource_type FROM smms.audit_log "
                f"WHERE org_id=:org_id{since_clause} "
                f"ORDER BY resource_type LIMIT 200"
            ), params)).fetchall()
            actors = (await s.execute(text(
                f"SELECT DISTINCT actor_id FROM smms.audit_log "
                f"WHERE org_id=:org_id{since_clause} "
                f"AND actor_id IS NOT NULL "
                f"ORDER BY actor_id LIMIT 200"
            ), params)).fetchall()
        return {
            "actions":         [r[0] for r in actions if r[0]],
            "resource_types":  [r[0] for r in rtypes if r[0]],
            "actor_ids":       [str(r[0]) for r in actors if r[0]],
        }

    async def list_recent(
        self,
        org_id: OrgId | str,
        *,
        limit: int = 200,
        action_prefix: str | None = None,
        resource_type: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["org_id = :org_id"]
        params: dict[str, Any] = {"org_id": UUID(str(org_id)), "limit": int(max(1, min(limit, 1000)))}
        if action_prefix:
            clauses.append("action LIKE :ap")
            params["ap"] = f"{action_prefix}%"
        if resource_type:
            clauses.append("resource_type = :rt")
            params["rt"] = resource_type
        if since is not None:
            clauses.append("occurred_at >= :since")
            params["since"] = since
        sql = (
            "SELECT id, org_id, actor_type, actor_id, action, resource_type, "
            "       resource_id, before, after, request_id, occurred_at "
            "FROM smms.audit_log "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY occurred_at DESC LIMIT :limit"
        )
        async with self._sm() as s:
            result = await s.execute(text(sql), params)
            rows = result.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            before = r.before if isinstance(r.before, dict) else (json.loads(r.before) if r.before else None)
            after = r.after if isinstance(r.after, dict) else (json.loads(r.after) if r.after else None)
            out.append({
                "id": str(r.id),
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else "",
                "actor_type": r.actor_type,
                "actor_id": str(r.actor_id) if r.actor_id else None,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": str(r.resource_id) if r.resource_id else None,
                "before": before,
                "after": after,
                "request_id": r.request_id,
            })
        return out


# ── cursor helpers ─────────────────────────────────────────────────────
def _encode_cursor(ts: datetime, row_id: UUID | str) -> str:
    """Opaque cursor = base64 of ``{"ts": iso8601, "id": uuid_str}``.

    Opaque so clients can't fabricate cursors that probe other orgs
    (the SQL still scopes by org_id, but principle-of-least-surprise:
    don't let cursors leak schema)."""
    payload = json.dumps({
        "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "id": str(row_id),
    }, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> dict | None:
    """Best-effort decode. Returns ``None`` for any garbled cursor —
    callers treat it as a fresh scan rather than 400-ing the request,
    because audit log reads are idempotent and the user is debugging."""
    try:
        # Re-pad — we stripped trailing '=' in encode.
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw)
        ts_raw = data.get("ts")
        id_raw = data.get("id")
        if not ts_raw or not id_raw:
            return None
        return {
            "ts": datetime.fromisoformat(ts_raw),
            "id": UUID(id_raw),
        }
    except Exception:                                                # noqa: BLE001
        return None
