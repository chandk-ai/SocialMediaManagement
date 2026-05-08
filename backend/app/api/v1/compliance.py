"""Compliance profile catalog — read-only listing for the workflow UI's
profile picker. The actual scan runs in-process via
:func:`app.services.compliance.scan_drafts`; this endpoint just exposes
the available profile names and human-readable descriptions."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import current_user
from app.core.security import Principal
from app.services.compliance import list_profiles

router = APIRouter()


@router.get("/compliance/profiles")
async def get_profiles(_: Principal = Depends(current_user)) -> dict:
    """Return the catalog of compliance profiles the workflow editor can
    pick from. Empty list = no profiles registered (none here would
    require redeploying the backend with new code, which is fine — these
    are policy decisions, not customer-configurable)."""
    return {
        "profiles": [
            {
                "name": p.name,
                "label": p.label,
                "description": p.description,
                "docs_url": p.docs_url or None,
                "rule_count": len(p.forbidden) + len(p.required_any),
            }
            for p in list_profiles()
        ],
    }
