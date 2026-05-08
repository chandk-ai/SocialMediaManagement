from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel

from app.adapters.sources.base import ContentSource, SourceConnectionError
from app.api.deps import (
    current_user,
    get_audit_log_service,
    get_plugin_service,
    get_source_service,
    get_workflow_service,
)
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId, SourceId
from app.plugins.registry import PluginKind
from app.schemas.sources import SourceCreate, SourceOut
from app.services.audit_log import AuditLogService
from app.services.plugin_service import PluginService
from app.services.source_secrets import (
    decrypt_config,
    encrypt_config,
    merge_update,
    redact_config,
)
from app.services.source_service import SourceService
from app.services.workflow_service import WorkflowService

router = APIRouter()


def _schema_for(plugins: PluginService, plugin_name: str) -> dict | None:
    entry = next(
        (p for p in plugins.registry.list(PluginKind.SOURCE) if p.name == plugin_name),
        None,
    )
    if entry is None:
        return None
    return getattr(entry.cls, "config_schema", None)


def _to_out(s, plugins: PluginService) -> SourceOut:
    return SourceOut(
        id=s.id, plugin_name=s.plugin_name, display_name=s.display_name,
        is_active=s.is_active,
        config=redact_config(s.config),                          # never leak ciphertext
        last_fetched_at=s.last_fetched_at,
        last_failure_at=s.last_failure_at,
        last_error=s.last_error,
        error_count=s.error_count,
        item_count=s.item_count,
        created_at=s.created_at,
    )


def _build_adapter(plugins: PluginService, plugin_name: str, config: dict) -> ContentSource:
    """Instantiate a ContentSource from registered plugins, given a config."""
    entry = next(
        (p for p in plugins.registry.list(PluginKind.SOURCE) if p.name == plugin_name),
        None,
    )
    if entry is None:
        raise HTTPException(
            status_code=400, detail=f"Source plugin {plugin_name!r} not registered",
        )
    return entry.cls(config=decrypt_config(config))


# ── list / create ─────────────────────────────────────────────────────────
@router.get("", response_model=list[SourceOut])
async def list_sources(
    user: Principal = Depends(current_user),
    svc: SourceService = Depends(get_source_service),
    plugins: PluginService = Depends(get_plugin_service),
) -> list[SourceOut]:
    items = await svc.list(OrgId(UUID(user.org_id)))
    return [_to_out(i, plugins) for i in items]


@router.post("", response_model=SourceOut, status_code=http_status.HTTP_201_CREATED)
async def create_source(
    body: SourceCreate,
    user: Principal = Depends(current_user),
    svc: SourceService = Depends(get_source_service),
    plugins: PluginService = Depends(get_plugin_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> SourceOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    schema = _schema_for(plugins, body.plugin_name)
    encrypted_config = encrypt_config(body.config or {}, schema)
    s = await svc.create(
        org_id=OrgId(UUID(user.org_id)),
        plugin_name=body.plugin_name,
        display_name=body.display_name,
        config=encrypted_config,
    )
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="source.create",
            resource_type="source",
            resource_id=s.id,
            after={"plugin_name": s.plugin_name, "display_name": s.display_name},
        )
    return _to_out(s, plugins)


# ── update / toggle / delete ──────────────────────────────────────────────
class SourceUpdateBody(BaseModel):
    display_name: str | None = None
    config: dict | None = None
    is_active: bool | None = None


async def _require_owned(svc: SourceService, org_id: OrgId, source_id: SourceId):
    s = await svc.repo.get(org_id, source_id)
    if s is None:
        raise HTTPException(status_code=404, detail="source not found")
    return s


@router.patch("/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: UUID,
    body: SourceUpdateBody,
    user: Principal = Depends(current_user),
    svc: SourceService = Depends(get_source_service),
    plugins: PluginService = Depends(get_plugin_service),
) -> SourceOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    s = await _require_owned(svc, OrgId(UUID(user.org_id)), SourceId(source_id))
    if body.display_name is not None: s.display_name = body.display_name
    if body.is_active is not None:    s.is_active = body.is_active
    if body.config is not None:
        schema = _schema_for(plugins, s.plugin_name)
        # Preserve unchanged secrets when the user leaves password fields blank.
        merged = merge_update(s.config, body.config, schema)
        s.config = encrypt_config(merged, schema)
    await svc.repo.update(s)
    return _to_out(s, plugins)


@router.delete("/{source_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: UUID,
    user: Principal = Depends(current_user),
    svc: SourceService = Depends(get_source_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> None:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    org_id = OrgId(UUID(user.org_id))
    existing = await svc.repo.get(org_id, SourceId(source_id))
    await svc.repo.delete(org_id, SourceId(source_id))
    if audit is not None and existing is not None:
        await audit.record(
            org_id=user.org_id,
            action="source.delete",
            resource_type="source",
            resource_id=source_id,
            before={"plugin_name": existing.plugin_name, "display_name": existing.display_name},
        )


@router.post("/{source_id}/clone", response_model=SourceOut, status_code=http_status.HTTP_201_CREATED)
async def clone_source(
    source_id: UUID,
    user: Principal = Depends(current_user),
    svc: SourceService = Depends(get_source_service),
    plugins: PluginService = Depends(get_plugin_service),
) -> SourceOut:
    """Duplicate an existing source — handy when a user wants to test config
    tweaks against a new copy without editing the live one."""
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    src = await _require_owned(svc, OrgId(UUID(user.org_id)), SourceId(source_id))
    s = await svc.create(
        org_id=src.org_id,
        plugin_name=src.plugin_name,
        display_name=f"{src.display_name} (copy)",
        config=dict(src.config),
    )
    return _to_out(s, plugins)


# ── test / preview / used-by ───────────────────────────────────────────────
class TestBody(BaseModel):
    """Test a config against the plugin without saving — used by the
    'Test before save' button in the Add form."""
    plugin_name: str
    config: dict | None = None


@router.post("/test")
async def test_unsaved(
    body: TestBody,
    user: Principal = Depends(current_user),
    plugins: PluginService = Depends(get_plugin_service),
) -> dict:
    """Validate a fresh config (e.g. while filling the Add form) before
    persisting anything. Returns ``{ok, error}`` so the UI can show ✅/❌."""
    try:
        adapter = _build_adapter(plugins, body.plugin_name, body.config or {})
        await adapter.connect()
        return {"ok": True}
    except SourceConnectionError as exc:
        return {"ok": False, "error": str(exc)}
    except HTTPException:
        raise
    except Exception as exc:                                     # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.post("/{source_id}/test")
async def test_source(
    source_id: UUID,
    user: Principal = Depends(current_user),
    svc: SourceService = Depends(get_source_service),
    plugins: PluginService = Depends(get_plugin_service),
) -> dict:
    """Run the plugin's `connect()` on the saved source and stamp health."""
    s = await _require_owned(svc, OrgId(UUID(user.org_id)), SourceId(source_id))
    try:
        adapter = _build_adapter(plugins, s.plugin_name, s.config)
        await adapter.connect()
        s.last_error = None
        s.error_count = 0
        await svc.repo.update(s)
        return {"ok": True}
    except SourceConnectionError as exc:
        s.mark_failure(str(exc))
        await svc.repo.update(s)
        return {"ok": False, "error": str(exc)}
    except Exception as exc:                                     # noqa: BLE001
        s.mark_failure(f"{type(exc).__name__}: {exc}")
        await svc.repo.update(s)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.post("/{source_id}/preview")
async def preview_source(
    source_id: UUID,
    user: Principal = Depends(current_user),
    svc: SourceService = Depends(get_source_service),
    plugins: PluginService = Depends(get_plugin_service),
) -> dict:
    """Pull up to N items so the user can sanity-check + warm the health stamp."""
    LIMIT = 5
    s = await _require_owned(svc, OrgId(UUID(user.org_id)), SourceId(source_id))
    try:
        adapter = _build_adapter(plugins, s.plugin_name, s.config)
        await adapter.connect()
        items: list[dict] = []
        async for item in adapter.fetch():
            items.append({
                "external_id": item.external_id,
                "title": item.title or "",
                "body": (item.body or "")[:500],
                "url": item.url,
                "published_at": item.published_at.isoformat() if item.published_at else None,
            })
            if len(items) >= LIMIT:
                break
        s.mark_success(items_yielded=len(items))
        await svc.repo.update(s)
        return {"ok": True, "count": len(items), "items": items}
    except SourceConnectionError as exc:
        s.mark_failure(str(exc))
        await svc.repo.update(s)
        return {"ok": False, "error": str(exc), "items": []}
    except Exception as exc:                                     # noqa: BLE001
        s.mark_failure(f"{type(exc).__name__}: {exc}")
        await svc.repo.update(s)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "items": []}


@router.get("/{source_id}/usage")
async def used_by_workflows(
    source_id: UUID,
    user: Principal = Depends(current_user),
    workflows_svc: WorkflowService = Depends(get_workflow_service),
) -> dict:
    """Which workflows reference this source — so users know the blast radius
    before deleting / pausing."""
    org_id = OrgId(UUID(user.org_id))
    workflows = await workflows_svc.list(org_id)
    matches = [
        {"id": str(w.id), "name": w.name, "status": w.status.value}
        for w in workflows
        if str(source_id) in [str(s) for s in w.source_ids]
    ]
    return {"count": len(matches), "workflows": matches}
