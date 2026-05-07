"""Encrypt / decrypt sensitive fields in a Source's `config` dict.

A field is "sensitive" when its JSON Schema declaration uses
``"format": "password"`` (matches our `JsonSchemaForm` convention on the
frontend). On write, the plaintext is replaced with a sentinel envelope:

    {"__secret__": True, "ciphertext": "<base64>"}

On read (or use), the envelope is decrypted via ``TokenVault``. Non-sensitive
fields are passed through unchanged.

Why this exists: GitHub PATs, S3 keys, Notion tokens, etc. are stored in
``sources.config`` (JSONB) on the database. Without encryption, anyone with
read access to that table sees plaintext credentials.
"""
from __future__ import annotations

import base64
from typing import Any

from app.core.secrets import build_token_vault
from app.domain.value_objects.credentials import EncryptedToken


_ENVELOPE_KEY = "__secret__"


def _is_envelope(value: Any) -> bool:
    return isinstance(value, dict) and value.get(_ENVELOPE_KEY) is True


def _sensitive_keys(schema: dict | None) -> set[str]:
    """Keys whose schema declares ``format: password`` are treated as secrets."""
    if not schema:
        return set()
    props = schema.get("properties") or {}
    return {
        k for k, prop in props.items()
        if isinstance(prop, dict) and prop.get("format") == "password"
    }


def encrypt_config(config: dict, schema: dict | None) -> dict:
    """Return a copy of ``config`` with sensitive fields wrapped in envelopes."""
    if not config:
        return config
    sensitive = _sensitive_keys(schema)
    out: dict[str, Any] = {}
    vault = build_token_vault() if sensitive else None
    for k, v in config.items():
        if k in sensitive and isinstance(v, str) and v and not _is_envelope(v):
            blob = vault.encrypt(v)                              # type: ignore[union-attr]
            out[k] = {
                _ENVELOPE_KEY: True,
                "ciphertext": base64.b64encode(blob.ciphertext).decode("ascii"),
            }
        else:
            out[k] = v
    return out


def decrypt_config(config: dict) -> dict:
    """Return a copy of ``config`` with envelopes decrypted to plaintext."""
    if not config:
        return config
    out: dict[str, Any] = {}
    vault = None
    for k, v in config.items():
        if _is_envelope(v):
            vault = vault or build_token_vault()
            try:
                ct = base64.b64decode(v.get("ciphertext", "").encode("ascii"))
                out[k] = vault.decrypt(EncryptedToken(ciphertext=ct, key_id=k))
            except Exception:                                    # noqa: BLE001
                # If decryption fails, fall back to empty rather than leaking.
                out[k] = ""
        else:
            out[k] = v
    return out


def redact_config(config: dict) -> dict:
    """Return a copy with sensitive envelopes replaced by ``"<encrypted>"`` —
    safe to surface in API responses to the frontend."""
    if not config:
        return config
    out: dict[str, Any] = {}
    for k, v in config.items():
        if _is_envelope(v):
            out[k] = ""           # show empty so the user can re-type if they want to update
        else:
            out[k] = v
    return out


def merge_update(saved: dict, incoming: dict, schema: dict | None) -> dict:
    """When the user PATCHes a config, sensitive fields they left BLANK should
    keep the previously-stored encrypted value. This preserves "leave password
    blank to keep it" semantics so they don't have to re-type secrets every edit.
    """
    if not incoming:
        return saved
    sensitive = _sensitive_keys(schema)
    merged: dict[str, Any] = {**saved, **incoming}
    for k in sensitive:
        if k in incoming and incoming[k] in (None, ""):
            # User left it blank → restore prior envelope (or absence).
            if k in saved:
                merged[k] = saved[k]
            else:
                merged.pop(k, None)
    return merged
