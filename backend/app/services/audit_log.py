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

import json
from datetime import datetime
from typing import Any
from uuid import UUID

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
