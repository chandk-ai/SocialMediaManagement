"""Team membership + invitation endpoints.

Implements the email-claim auto-join model: an admin POSTs an invitation
with an email + role, and the row sits in ``smms.org_invitations`` until
the invited person signs in via Supabase Auth (any method — OAuth, magic
link). At that point the auth path resolves their Principal by matching
the email and promotes them to a member at the invited role. There's no
invite token to share, no email to send: the admin tells the teammate to
sign in, and the system does the rest.

Endpoints:
  GET    /team/members                  — list current members
  PATCH  /team/members/{user_id}/role   — admin only; change role
  DELETE /team/members/{user_id}        — admin only; remove (refuses last admin)
  GET    /team/invitations              — list pending invitations
  POST   /team/invitations              — admin only; create pending invite
  DELETE /team/invitations/{id}         — admin only; revoke pending invite
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import (
    current_user,
    get_audit_log_service,
    get_team_service,
)
from app.core.security import Principal
from app.domain.entities.user import Role
from app.domain.value_objects.ids import OrgId
from app.services.audit_log import AuditLogService
from app.services.team import TeamService

router = APIRouter()


def _require(svc: TeamService | None) -> TeamService:
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="Team service not available — backend is in memory mode.",
        )
    return svc


def _require_admin(user: Principal) -> None:
    if not user.role.can_admin():
        raise HTTPException(status_code=403, detail="Admin role required")


# ── members ─────────────────────────────────────────────────────────────
@router.get("/team/members")
async def list_members(
    user: Principal = Depends(current_user),
    svc: TeamService | None = Depends(get_team_service),
) -> dict:
    s = _require(svc)
    rows = await s.list_members(OrgId(UUID(user.org_id)))
    return {
        "members": [
            {
                "user_id": m.user_id,
                "email": m.email,
                "display_name": m.display_name,
                "role": m.role.value,
                "is_active": m.is_active,
                "created_at": m.created_at,
                "claimed": m.supabase_uid is not None,
            }
            for m in rows
        ],
    }


class RoleUpdateBody(BaseModel):
    role: Role


@router.patch("/team/members/{user_id}/role")
async def update_member_role(
    user_id: UUID,
    body: RoleUpdateBody,
    user: Principal = Depends(current_user),
    svc: TeamService | None = Depends(get_team_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    _require_admin(user)
    s = _require(svc)
    # `Principal.subject` is the Supabase auth UID; the URL `user_id` is the
    # local smms.users.id. Translate before comparing so the self-check
    # actually fires.
    me_local_id = await s.resolve_local_user_id(OrgId(UUID(user.org_id)), user.subject)
    if me_local_id is not None and user_id == me_local_id and body.role is not Role.ADMIN:
        # Block self-demotion as a foot-gun guard. Admins can demote
        # themselves only via removing-and-recreating with a peer admin's
        # action, which is intentional friction.
        raise HTTPException(
            status_code=400,
            detail="You can't demote your own admin role. Have another admin do it.",
        )
    try:
        m = await s.update_role(OrgId(UUID(user.org_id)), str(user_id), body.role)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="team.member.role_change",
            resource_type="user",
            resource_id=user_id,
            after={"role": body.role.value, "email": m.email},
        )
    return {
        "user_id": m.user_id,
        "email": m.email,
        "display_name": m.display_name,
        "role": m.role.value,
        "is_active": m.is_active,
    }


@router.delete("/team/members/{user_id}", status_code=204)
async def remove_member(
    user_id: UUID,
    user: Principal = Depends(current_user),
    svc: TeamService | None = Depends(get_team_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> None:
    _require_admin(user)
    s = _require(svc)
    me_local_id = await s.resolve_local_user_id(OrgId(UUID(user.org_id)), user.subject)
    if me_local_id is not None and user_id == me_local_id:
        raise HTTPException(
            status_code=400,
            detail="You can't remove yourself. Have another admin do it.",
        )
    try:
        ok = await s.remove_member(OrgId(UUID(user.org_id)), str(user_id))
    except ValueError as exc:
        # last-admin guard
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Member not found.")
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="team.member.remove",
            resource_type="user",
            resource_id=user_id,
        )


# ── invitations ─────────────────────────────────────────────────────────
class InvitationBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)   # RFC 5321 max length
    role: Role = Role.VIEWER


@router.get("/team/invitations")
async def list_invitations(
    user: Principal = Depends(current_user),
    svc: TeamService | None = Depends(get_team_service),
) -> dict:
    s = _require(svc)
    rows = await s.list_invitations(OrgId(UUID(user.org_id)))
    return {
        "invitations": [
            {
                "id": r.id,
                "email": r.email,
                "role": r.role.value,
                "invited_by": r.invited_by,
                "invited_at": r.invited_at,
            }
            for r in rows
        ],
    }


@router.post("/team/invitations", status_code=201)
async def create_invitation(
    body: InvitationBody,
    user: Principal = Depends(current_user),
    svc: TeamService | None = Depends(get_team_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    _require_admin(user)
    s = _require(svc)
    try:
        row = await s.create_invitation(
            OrgId(UUID(user.org_id)),
            email=str(body.email),
            role=body.role,
            invited_by=user.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="team.invite.create",
            resource_type="invitation",
            resource_id=row.id,
            after={"email": row.email, "role": row.role.value},
        )
    return {
        "id": row.id,
        "email": row.email,
        "role": row.role.value,
        "invited_at": row.invited_at,
        # The invitee just needs to sign in with this email — there is no
        # token-bearing URL. Surface the message we want the admin to relay.
        "instruction": (
            f"Tell {row.email} to sign in at the app URL with that email. "
            "They'll be auto-joined to this org as " + row.role.value + "."
        ),
    }


@router.delete("/team/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    invitation_id: UUID,
    user: Principal = Depends(current_user),
    svc: TeamService | None = Depends(get_team_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> None:
    _require_admin(user)
    s = _require(svc)
    ok = await s.revoke_invitation(OrgId(UUID(user.org_id)), str(invitation_id))
    if not ok:
        raise HTTPException(status_code=404, detail="Invitation not found or already claimed.")
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="team.invite.revoke",
            resource_type="invitation",
            resource_id=invitation_id,
        )
