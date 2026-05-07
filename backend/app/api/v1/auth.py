"""Auth-related endpoints. Most auth happens via Okta; we just expose:
- /auth/me   — return the current verified principal
- /auth/dev-token — convenience helper to mint dev tokens (dev env only)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import current_user
from app.core.config import Settings, get_settings
from app.core.security import Principal

router = APIRouter()


@router.get("/me")
async def me(user: Principal = Depends(current_user)) -> dict:
    return {
        "subject": user.subject,
        "email": user.email,
        "name": user.name,
        "org_id": user.org_id,
        "role": user.role.value,
    }


@router.post("/dev-token")
async def dev_token(role: str = "admin", email: str = "dev@example.com",
                    org_id: str = "00000000-0000-0000-0000-000000000001",
                    settings: Settings = Depends(get_settings)) -> dict:
    if settings.env not in {"dev", "test"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not available")
    return {"token": f"dev.{role}.{email}.{org_id}"}
