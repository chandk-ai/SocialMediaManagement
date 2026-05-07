from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import current_user, get_source_service
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId
from app.schemas.sources import SourceCreate, SourceOut
from app.services.source_service import SourceService

router = APIRouter()


@router.get("", response_model=list[SourceOut])
async def list_sources(
    user: Principal = Depends(current_user),
    svc: SourceService = Depends(get_source_service),
) -> list[SourceOut]:
    items = await svc.list(OrgId(UUID(user.org_id)))
    return [SourceOut(
        id=i.id, plugin_name=i.plugin_name, display_name=i.display_name,
        is_active=i.is_active, config=i.config,
        last_fetched_at=i.last_fetched_at, created_at=i.created_at,
    ) for i in items]


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(
    body: SourceCreate,
    user: Principal = Depends(current_user),
    svc: SourceService = Depends(get_source_service),
) -> SourceOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    s = await svc.create(
        org_id=OrgId(UUID(user.org_id)),
        plugin_name=body.plugin_name,
        display_name=body.display_name,
        config=body.config,
    )
    return SourceOut(
        id=s.id, plugin_name=s.plugin_name, display_name=s.display_name,
        is_active=s.is_active, config=s.config,
        last_fetched_at=s.last_fetched_at, created_at=s.created_at,
    )
