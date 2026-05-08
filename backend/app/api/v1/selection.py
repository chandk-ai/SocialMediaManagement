"""Selection-strategy catalog — read-only listing for the workflow wizard's
strategy picker. Same pattern as /compliance/profiles."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import current_user, get_registry
from app.core.security import Principal
from app.plugins.registry import PluginKind, PluginRegistry

router = APIRouter()


@router.get("/selection/strategies")
async def list_strategies(
    _: Principal = Depends(current_user),
    registry: PluginRegistry = Depends(get_registry),
) -> dict:
    """Every registered selection strategy + its JSON Schema config.
    The wizard renders the schema-driven config form when the user picks
    a strategy that has fields beyond the defaults."""
    out = []
    for entry in registry.list(PluginKind.SELECTION):
        cls = entry.cls
        out.append({
            "name": entry.name,
            "display_name": getattr(cls, "display_name", entry.name),
            "description": getattr(cls, "description", ""),
            "config_schema": getattr(cls, "config_schema", {"type": "object"}),
        })
    out.sort(key=lambda x: x["name"])
    return {"strategies": out}
