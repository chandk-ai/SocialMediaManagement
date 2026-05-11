"""Plugin marketplace — Pillar 6.

Exposes the loaded registry as a browseable catalog plus a JSON-Schema
validator for plugin configs. The frontend page at /admin/plugins
consumes:

    GET /marketplace                  — full catalog grouped by kind
    GET /marketplace/{kind}/{name}    — full schema + metadata
    POST /marketplace/validate        — validate a config payload
                                         against a plugin's schema

The catalog is read-only here. Authoring flow (CLI scaffold + drop-in
to plugins/ directory) lives outside the API for now — by design,
runtime plugin loading is a deployment-time concern.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import current_user, get_registry
from app.core.security import Principal
from app.plugins.registry import PluginKind, PluginRegistry

router = APIRouter()


def _ser_entry(entry) -> dict[str, Any]:
    """Serialize a PluginEntry into the marketplace shape. ``entry.cls``
    is the registered class — we pull display metadata + config_schema
    from there since that's where @register_plugin decorators stash it."""
    cls = entry.cls
    name = entry.name
    return {
        "kind": entry.kind.value,
        "name": name,
        "display_name": getattr(cls, "display_name", "") or name,
        "description": getattr(cls, "description", "") or "",
        "api_version": entry.api_version,
        "category": (entry.metadata.get("category")
                     if entry.metadata else None)
                    or getattr(cls, "category", "builtin"),
        "needs_llm": bool(getattr(cls, "needs_llm", False)),
        "experimental": bool(
            (entry.metadata or {}).get("experimental")
            or getattr(cls, "experimental", False)
        ),
        "config_schema": getattr(
            cls, "config_schema", {"type": "object", "properties": {}}
        ),
    }


@router.get("/marketplace")
async def catalog(
    user: Principal = Depends(current_user),
    registry: PluginRegistry = Depends(get_registry),
) -> dict:
    """Group all loaded plugins by kind. The same data drives the
    workflow wizard's plugin pickers — keeping a single source of
    truth between admin marketplace + per-feature pickers."""
    out: dict[str, list[dict[str, Any]]] = {}
    for kind in PluginKind:
        items = [_ser_entry(e) for e in registry.list(kind)]
        items.sort(key=lambda p: (p["display_name"] or "").lower())
        out[kind.value] = items
    return {"kinds": out, "total": sum(len(v) for v in out.values())}


@router.get("/marketplace/{kind}/{name}")
async def get_plugin(
    kind: str, name: str,
    user: Principal = Depends(current_user),
    registry: PluginRegistry = Depends(get_registry),
) -> dict:
    try:
        kind_enum = PluginKind(kind)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown kind: {kind}")
    try:
        entry = registry.get(kind_enum, name)
    except Exception:                                                # noqa: BLE001
        raise HTTPException(status_code=404,
                             detail=f"plugin {kind}/{name} not found")
    return _ser_entry(entry)


class ValidateBody(BaseModel):
    kind: str
    name: str
    config: dict[str, Any]


@router.post("/marketplace/validate")
async def validate_config(
    body: ValidateBody,
    user: Principal = Depends(current_user),
    registry: PluginRegistry = Depends(get_registry),
) -> dict:
    """Server-side JSON-Schema validation. Lets the wizard surface
    field-level errors without baking schema knowledge into the UI."""
    try:
        kind_enum = PluginKind(body.kind)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown kind: {body.kind}")
    try:
        entry = registry.get(kind_enum, body.name)
    except Exception:                                                # noqa: BLE001
        raise HTTPException(status_code=404,
                             detail=f"plugin {body.kind}/{body.name} not found")
    schema = getattr(entry.cls, "config_schema",
                      {"type": "object", "properties": {}})
    errors = _validate(body.config, schema)
    return {"ok": not errors, "errors": errors}


def _validate(payload: Any, schema: dict) -> list[dict]:
    """Tiny JSON-Schema-ish validator — covers the subset our plugin
    schemas use (object/string/integer/number/boolean/enum/required/
    minimum/maximum/minLength/maxLength/pattern). Avoids pulling in
    a heavyweight dep just for this; if customers go richer with
    their schemas, swap in `jsonschema` here."""
    errors: list[dict] = []
    _validate_node(payload, schema, "$", errors)
    return errors


def _validate_node(node: Any, schema: dict, path: str,
                    errors: list[dict]) -> None:
    if not isinstance(schema, dict):
        return
    t = schema.get("type")
    if t == "object":
        if not isinstance(node, dict):
            errors.append({"path": path, "msg": "expected object"})
            return
        for key in schema.get("required") or []:
            if key not in node:
                errors.append({"path": f"{path}.{key}",
                                "msg": "required"})
        props = schema.get("properties") or {}
        for k, v in node.items():
            sub = props.get(k)
            if sub is not None:
                _validate_node(v, sub, f"{path}.{k}", errors)
    elif t == "string":
        if not isinstance(node, str):
            errors.append({"path": path, "msg": "expected string"})
            return
        if "minLength" in schema and len(node) < int(schema["minLength"]):
            errors.append({"path": path,
                            "msg": f"min length {schema['minLength']}"})
        if "maxLength" in schema and len(node) > int(schema["maxLength"]):
            errors.append({"path": path,
                            "msg": f"max length {schema['maxLength']}"})
        if "enum" in schema and node not in schema["enum"]:
            errors.append({"path": path,
                            "msg": f"must be one of {schema['enum']}"})
        if "pattern" in schema:
            import re
            if not re.search(schema["pattern"], node):
                errors.append({"path": path,
                                "msg": f"must match {schema['pattern']}"})
    elif t in ("integer", "number"):
        if t == "integer" and not isinstance(node, int):
            errors.append({"path": path, "msg": "expected integer"})
            return
        if t == "number" and not isinstance(node, (int, float)):
            errors.append({"path": path, "msg": "expected number"})
            return
        if "minimum" in schema and node < schema["minimum"]:
            errors.append({"path": path,
                            "msg": f"min {schema['minimum']}"})
        if "maximum" in schema and node > schema["maximum"]:
            errors.append({"path": path,
                            "msg": f"max {schema['maximum']}"})
    elif t == "boolean":
        if not isinstance(node, bool):
            errors.append({"path": path, "msg": "expected boolean"})
    elif t == "array":
        if not isinstance(node, list):
            errors.append({"path": path, "msg": "expected array"})
            return
        items_schema = schema.get("items")
        if items_schema:
            for i, v in enumerate(node):
                _validate_node(v, items_schema, f"{path}[{i}]", errors)
        if "minItems" in schema and len(node) < int(schema["minItems"]):
            errors.append({"path": path,
                            "msg": f"min items {schema['minItems']}"})
        if "maxItems" in schema and len(node) > int(schema["maxItems"]):
            errors.append({"path": path,
                            "msg": f"max items {schema['maxItems']}"})
