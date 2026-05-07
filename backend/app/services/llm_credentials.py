"""Org-level LLM credential storage.

API keys are encrypted at rest via the same TokenVault used for OAuth
secrets. Keys live on ``organizations.llm_credentials`` (JSONB) — keyed by
provider plugin name (``anthropic``, ``openai``, ``google``, ``groq``, etc).

A secret envelope looks like:
    { "anthropic": { "ciphertext_b64": "...", "key_id": "anthropic" } }

Use ``store_key()`` to set, ``decrypt_key()`` to fetch the plaintext at
runtime (e.g. when the agent loop instantiates a provider), and
``list_providers_with_keys()`` for the UI to render which keys exist
without leaking ciphertext.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.secrets import build_token_vault
from app.domain.value_objects.credentials import EncryptedToken
from app.domain.value_objects.ids import OrgId

KNOWN_PROVIDERS = (
    "anthropic", "openai", "google", "gemini", "groq", "azure_openai",
    "huggingface", "ollama", "bedrock",
)


@dataclass(frozen=True, slots=True)
class LlmKeyInfo:
    provider: str
    is_set: bool
    last_4: str | None       # last 4 chars to identify the key (never the secret)


class LlmCredentialsService:
    """Persists and retrieves org-level LLM API keys."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sm = session_factory
        self._vault = build_token_vault()

    async def store_key(self, org_id: OrgId, provider: str, plaintext: str) -> None:
        if provider not in KNOWN_PROVIDERS:
            raise ValueError(f"Unknown LLM provider: {provider!r}")
        if not plaintext:
            raise ValueError("API key cannot be empty")
        envelope = {
            "ciphertext_b64": base64.b64encode(
                self._vault.encrypt(plaintext).ciphertext,
            ).decode("ascii"),
            "key_id": provider,
            "last_4": plaintext[-4:],
        }
        async with self._sm() as s:
            await s.execute(
                text(
                    "UPDATE smms.organizations "
                    "SET llm_credentials = jsonb_set("
                    "    coalesce(llm_credentials, '{}'::jsonb), "
                    "    array[:provider], "
                    "    cast(:envelope as jsonb), "
                    "    true) "
                    "WHERE id = :org_id"
                ),
                {
                    "org_id": UUID(str(org_id)),
                    "provider": provider,
                    "envelope": __import__("json").dumps(envelope),
                },
            )
            await s.commit()

    async def remove_key(self, org_id: OrgId, provider: str) -> None:
        async with self._sm() as s:
            await s.execute(
                text(
                    "UPDATE smms.organizations "
                    "SET llm_credentials = llm_credentials - :provider "
                    "WHERE id = :org_id"
                ),
                {"org_id": UUID(str(org_id)), "provider": provider},
            )
            await s.commit()

    async def decrypt_key(self, org_id: OrgId, provider: str) -> str | None:
        """Return the plaintext API key for a provider, or None if not set."""
        async with self._sm() as s:
            row = (await s.execute(
                text(
                    "SELECT llm_credentials -> :provider AS env "
                    "FROM smms.organizations WHERE id = :org_id"
                ),
                {"org_id": UUID(str(org_id)), "provider": provider},
            )).first()
        if not row or not row.env:
            return None
        env = row.env if isinstance(row.env, dict) else __import__("json").loads(row.env)
        ct_b64 = env.get("ciphertext_b64")
        if not ct_b64:
            return None
        try:
            return self._vault.decrypt(EncryptedToken(
                ciphertext=base64.b64decode(ct_b64.encode("ascii")),
                key_id=provider,
            ))
        except Exception:                                       # noqa: BLE001
            return None

    async def list_providers_with_keys(self, org_id: OrgId) -> list[LlmKeyInfo]:
        """Surface which providers have keys configured (without revealing them)."""
        async with self._sm() as s:
            row = (await s.execute(
                text(
                    "SELECT llm_credentials FROM smms.organizations WHERE id = :org_id"
                ),
                {"org_id": UUID(str(org_id))},
            )).first()
        creds: dict[str, Any] = (row.llm_credentials if row else {}) or {}
        return [
            LlmKeyInfo(
                provider=name,
                is_set=name in creds,
                last_4=(creds.get(name) or {}).get("last_4") if name in creds else None,
            )
            for name in KNOWN_PROVIDERS
        ]

    async def get_preferred(self, org_id: OrgId) -> tuple[str | None, str | None]:
        """Return (provider, model) the user has chosen as default."""
        async with self._sm() as s:
            row = (await s.execute(
                text(
                    "SELECT preferred_llm_provider AS p, preferred_llm_model AS m "
                    "FROM smms.organizations WHERE id = :org_id"
                ),
                {"org_id": UUID(str(org_id))},
            )).first()
        return (row.p, row.m) if row else (None, None)

    async def set_preferred(
        self, org_id: OrgId, provider: str | None, model: str | None,
    ) -> None:
        async with self._sm() as s:
            await s.execute(
                text(
                    "UPDATE smms.organizations "
                    "SET preferred_llm_provider = :p, preferred_llm_model = :m "
                    "WHERE id = :org_id"
                ),
                {"org_id": UUID(str(org_id)), "p": provider, "m": model},
            )
            await s.commit()
