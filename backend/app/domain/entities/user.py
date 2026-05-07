from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..value_objects.ids import OrgId, UserId, new_id


class Role(str, Enum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"

    def can_edit(self) -> bool:
        return self in {Role.EDITOR, Role.ADMIN}

    def can_admin(self) -> bool:
        return self is Role.ADMIN


@dataclass(slots=True)
class User:
    id: UserId
    org_id: OrgId
    email: str
    display_name: str
    role: Role
    okta_subject: str          # `sub` claim from Okta JWT
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def create(
        cls, *, org_id: OrgId, email: str, display_name: str,
        role: Role, okta_subject: str,
    ) -> "User":
        return cls(
            id=UserId(new_id()),
            org_id=org_id,
            email=email.lower().strip(),
            display_name=display_name,
            role=role,
            okta_subject=okta_subject,
        )
