"""Team membership + invitation service.

Implements the *email-claim* auto-join flow: an admin creates a row in
``smms.org_invitations`` for ``alice@acme.com``; when Alice next signs in
via Supabase Auth, ``resolve_principal()`` matches by email and promotes
her to a real ``smms.users`` row at the invited role. No invite tokens, no
email sent — the email itself is the claim key. Designed for internal
admin tools where the URL is shared out-of-band.

Resolution order (in :meth:`resolve_principal`):

1. ``smms.users.supabase_uid`` matches → return that user's org/role.
2. ``smms.users.email`` matches → backfill ``supabase_uid`` and return.
3. Unclaimed ``smms.org_invitations`` matches by email → create user,
   mark invitation claimed, audit-log, return new principal.
4. None of the above → return ``None`` so the caller falls back to the
   placeholder/viewer behaviour (matches pre-existing semantics).

The service intentionally swallows audit-log failures (audit must never
break auth) but propagates DB errors so genuine outages surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.logging import get_logger
from app.domain.entities.user import Role
from app.domain.value_objects.ids import OrgId

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedPrincipal:
    """The pieces of identity our auth path needs."""
    user_id: str
    org_id: str
    role: Role
    email: str
    display_name: str


@dataclass(frozen=True, slots=True)
class MemberRow:
    user_id: str
    email: str
    display_name: str
    role: Role
    is_active: bool
    created_at: str
    supabase_uid: str | None


@dataclass(frozen=True, slots=True)
class InvitationRow:
    id: str
    email: str
    role: Role
    invited_by: str | None
    invited_at: str


class TeamService:
    """Pure-DB service. No external API calls — everything happens via SQL.

    Methods that mutate `smms.users` / `smms.org_invitations` accept an
    optional :class:`AuditLogService` so callers can record the action with
    correct request_id / user context. Passing ``audit=None`` is fine for
    the auto-claim path which runs from inside auth and may not have a
    fully-formed Principal yet.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sm = session_factory

    # ── auto-claim resolution ───────────────────────────────────────────
    async def resolve_principal(
        self,
        *,
        supabase_uid: str,
        email: str,
        display_name: str,
    ) -> ResolvedPrincipal | None:
        """Look up (or create-on-claim) the local membership for a Supabase
        user. Returns ``None`` if no match and no pending invite exists."""
        email_l = (email or "").lower().strip()

        async with self._sm() as s:
            # 1. supabase_uid match — fastest path, no email-string compares.
            row = (await s.execute(
                text(
                    "SELECT id, org_id, email, display_name, role "
                    "FROM smms.users WHERE supabase_uid = :uid"
                ),
                {"uid": UUID(supabase_uid)},
            )).first()
            if row:
                return ResolvedPrincipal(
                    user_id=str(row.id),
                    org_id=str(row.org_id),
                    role=Role(row.role),
                    email=row.email,
                    display_name=row.display_name,
                )

            # 2. email match — pre-provisioned user; backfill supabase_uid.
            if email_l:
                row = (await s.execute(
                    text(
                        "SELECT id, org_id, email, display_name, role "
                        "FROM smms.users WHERE lower(email) = :email LIMIT 1"
                    ),
                    {"email": email_l},
                )).first()
                if row:
                    await s.execute(
                        text(
                            "UPDATE smms.users SET supabase_uid = :uid "
                            "WHERE id = :id AND supabase_uid IS NULL"
                        ),
                        {"uid": UUID(supabase_uid), "id": row.id},
                    )
                    await s.commit()
                    return ResolvedPrincipal(
                        user_id=str(row.id),
                        org_id=str(row.org_id),
                        role=Role(row.role),
                        email=row.email,
                        display_name=row.display_name,
                    )

            # 3. pending invitation — create the user row + claim the invite.
            if email_l:
                inv = (await s.execute(
                    text(
                        "SELECT id, org_id, role FROM smms.org_invitations "
                        "WHERE lower(email) = :email "
                        "  AND claimed_at IS NULL AND revoked_at IS NULL "
                        "ORDER BY invited_at ASC LIMIT 1"
                    ),
                    {"email": email_l},
                )).first()
                if inv:
                    new_user_id = uuid4()
                    await s.execute(
                        text(
                            "INSERT INTO smms.users "
                            "(id, org_id, email, display_name, role, supabase_uid) "
                            "VALUES (:id, :org_id, :email, :name, :role, :uid)"
                        ),
                        {
                            "id": new_user_id,
                            "org_id": inv.org_id,
                            "email": email_l,
                            "name": display_name or email_l,
                            "role": inv.role,
                            "uid": UUID(supabase_uid),
                        },
                    )
                    await s.execute(
                        text(
                            "UPDATE smms.org_invitations "
                            "SET claimed_at = now(), claimed_by = :uid "
                            "WHERE id = :id"
                        ),
                        {"uid": new_user_id, "id": inv.id},
                    )
                    await s.commit()
                    log.info(
                        "team_invitation_claimed",
                        org_id=str(inv.org_id),
                        user_id=str(new_user_id),
                        email=email_l,
                        role=inv.role,
                    )
                    return ResolvedPrincipal(
                        user_id=str(new_user_id),
                        org_id=str(inv.org_id),
                        role=Role(inv.role),
                        email=email_l,
                        display_name=display_name or email_l,
                    )

        # 4. no match — caller falls back to placeholder behaviour.
        return None

    # ── invitations ─────────────────────────────────────────────────────
    async def list_invitations(self, org_id: OrgId) -> list[InvitationRow]:
        async with self._sm() as s:
            result = await s.execute(
                text(
                    "SELECT id, email, role, invited_by, invited_at "
                    "FROM smms.org_invitations "
                    "WHERE org_id = :org_id "
                    "  AND claimed_at IS NULL AND revoked_at IS NULL "
                    "ORDER BY invited_at DESC"
                ),
                {"org_id": UUID(str(org_id))},
            )
            rows = result.fetchall()
        return [
            InvitationRow(
                id=str(r.id),
                email=r.email,
                role=Role(r.role),
                invited_by=str(r.invited_by) if r.invited_by else None,
                invited_at=r.invited_at.isoformat() if r.invited_at else "",
            )
            for r in rows
        ]

    async def create_invitation(
        self,
        org_id: OrgId,
        *,
        email: str,
        role: Role,
        invited_by: str | None,
    ) -> InvitationRow:
        """``invited_by`` is the caller's auth identity — for Supabase that's
        the auth.users UUID, which lives on ``smms.users.supabase_uid``, not
        on ``smms.users.id``. The FK on ``org_invitations.invited_by``
        points to ``smms.users.id``, so we resolve here. If we can't find a
        local user row (e.g. the placeholder default org), we fall back to
        ``NULL`` — the column is nullable on purpose."""
        email_l = (email or "").lower().strip()
        if "@" not in email_l:
            raise ValueError("Email is required and must contain '@'.")
        # Surface a clean error if the email already belongs to a member.
        async with self._sm() as s:
            existing_member = (await s.execute(
                text(
                    "SELECT 1 FROM smms.users "
                    "WHERE org_id = :org_id AND lower(email) = :email"
                ),
                {"org_id": UUID(str(org_id)), "email": email_l},
            )).first()
            if existing_member:
                raise ValueError(f"{email_l} is already a member of this org.")

            # Resolve invited_by: try smms.users.id first, then supabase_uid.
            inviter_local_id = await self._resolve_local_user_id(s, org_id, invited_by)

            new_id = uuid4()
            await s.execute(
                text(
                    "INSERT INTO smms.org_invitations "
                    "(id, org_id, email, role, invited_by) "
                    "VALUES (:id, :org_id, :email, :role, :inviter)"
                ),
                {
                    "id": new_id,
                    "org_id": UUID(str(org_id)),
                    "email": email_l,
                    "role": role.value,
                    "inviter": inviter_local_id,
                },
            )
            await s.commit()
            row = (await s.execute(
                text(
                    "SELECT id, email, role, invited_by, invited_at "
                    "FROM smms.org_invitations WHERE id = :id"
                ),
                {"id": new_id},
            )).first()
        return InvitationRow(
            id=str(row.id),
            email=row.email,
            role=Role(row.role),
            invited_by=str(row.invited_by) if row.invited_by else None,
            invited_at=row.invited_at.isoformat() if row.invited_at else "",
        )

    async def revoke_invitation(
        self, org_id: OrgId, invitation_id: str,
    ) -> bool:
        async with self._sm() as s:
            result = await s.execute(
                text(
                    "UPDATE smms.org_invitations "
                    "SET revoked_at = now() "
                    "WHERE id = :id AND org_id = :org_id "
                    "  AND claimed_at IS NULL AND revoked_at IS NULL"
                ),
                {"id": UUID(invitation_id), "org_id": UUID(str(org_id))},
            )
            await s.commit()
            return result.rowcount > 0

    # ── members ─────────────────────────────────────────────────────────
    async def list_members(self, org_id: OrgId) -> list[MemberRow]:
        async with self._sm() as s:
            result = await s.execute(
                text(
                    "SELECT id, email, display_name, role, is_active, "
                    "       created_at, supabase_uid "
                    "FROM smms.users "
                    "WHERE org_id = :org_id "
                    "ORDER BY created_at ASC"
                ),
                {"org_id": UUID(str(org_id))},
            )
            rows = result.fetchall()
        return [
            MemberRow(
                user_id=str(r.id),
                email=r.email,
                display_name=r.display_name or r.email,
                role=Role(r.role),
                is_active=bool(r.is_active),
                created_at=r.created_at.isoformat() if r.created_at else "",
                supabase_uid=str(r.supabase_uid) if r.supabase_uid else None,
            )
            for r in rows
        ]

    async def update_role(
        self, org_id: OrgId, user_id: str, new_role: Role,
    ) -> MemberRow:
        async with self._sm() as s:
            result = await s.execute(
                text(
                    "UPDATE smms.users SET role = :role "
                    "WHERE id = :user_id AND org_id = :org_id"
                ),
                {
                    "role": new_role.value,
                    "user_id": UUID(user_id),
                    "org_id": UUID(str(org_id)),
                },
            )
            if result.rowcount == 0:
                raise ValueError("Member not found in this org.")
            await s.commit()
        members = await self.list_members(org_id)
        for m in members:
            if m.user_id == user_id:
                return m
        raise ValueError("Member vanished after update.")

    async def remove_member(self, org_id: OrgId, user_id: str) -> bool:
        async with self._sm() as s:
            # Prevent removing the last admin so the org never becomes
            # un-administrable. Same-transaction check + delete.
            row = (await s.execute(
                text(
                    "SELECT role FROM smms.users "
                    "WHERE id = :user_id AND org_id = :org_id"
                ),
                {"user_id": UUID(user_id), "org_id": UUID(str(org_id))},
            )).first()
            if not row:
                return False
            if row.role == "admin":
                admin_count = (await s.execute(
                    text(
                        "SELECT count(*) AS n FROM smms.users "
                        "WHERE org_id = :org_id AND role = 'admin' AND is_active = true"
                    ),
                    {"org_id": UUID(str(org_id))},
                )).scalar_one()
                if admin_count <= 1:
                    raise ValueError(
                        "Cannot remove the last admin. Promote another member first."
                    )
            await s.execute(
                text(
                    "DELETE FROM smms.users "
                    "WHERE id = :user_id AND org_id = :org_id"
                ),
                {"user_id": UUID(user_id), "org_id": UUID(str(org_id))},
            )
            await s.commit()
            return True

    # ── helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _maybe_uuid(s: str | None) -> UUID | None:
        if not s:
            return None
        try:
            return UUID(str(s))
        except (ValueError, TypeError):
            return None

    async def resolve_local_user_id(
        self, org_id: OrgId, identity: str | None,
    ) -> UUID | None:
        """Public wrapper — opens its own session. Use from API routes that
        need to translate ``Principal.subject`` (Supabase UID) to the
        ``smms.users.id`` foreign key the DB enforces."""
        async with self._sm() as s:
            return await self._resolve_local_user_id(s, org_id, identity)

    async def _resolve_local_user_id(
        self,
        session,
        org_id: OrgId,
        identity: str | None,
    ) -> UUID | None:
        """Map an auth identity (Supabase UID, dev-token subject, or already-
        local users.id) to the row's primary key in ``smms.users``.

        Order of attempts:
          1. ``smms.users.id`` matches directly  → caller already passed local id.
          2. ``smms.users.supabase_uid`` matches → caller passed the auth UID.
          3. Nothing matches → return None and let the caller insert NULL.

        Scoped to the org_id so the FK relationship stays tenant-correct.
        """
        candidate = self._maybe_uuid(identity)
        if candidate is None:
            return None
        # Direct id match — already a local users.id.
        row = (await session.execute(
            text(
                "SELECT id FROM smms.users "
                "WHERE id = :uid AND org_id = :org_id LIMIT 1"
            ),
            {"uid": candidate, "org_id": UUID(str(org_id))},
        )).first()
        if row:
            return row.id
        # Supabase UID match — translate.
        row = (await session.execute(
            text(
                "SELECT id FROM smms.users "
                "WHERE supabase_uid = :uid AND org_id = :org_id LIMIT 1"
            ),
            {"uid": candidate, "org_id": UUID(str(org_id))},
        )).first()
        if row:
            return row.id
        return None
