from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_plugin_service
from app.plugins.registry import PluginKind
from app.schemas.plugins import PluginInfo
from app.services.plugin_service import PluginService

router = APIRouter()


@router.get("", response_model=list[PluginInfo])
async def list_plugins(
    kind: str | None = Query(None, description="platform | source | llm"),
    svc: PluginService = Depends(get_plugin_service),
) -> list[PluginInfo]:
    kind_enum = PluginKind(kind) if kind else None
    return svc.list(kind_enum)
