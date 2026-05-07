"""Credential value objects. Sensitive material is wrapped in EncryptedToken
which never returns its raw value through __repr__ / __str__."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class EncryptedToken:
    """Opaque blob — actual decryption happens in the secrets service."""
    ciphertext: bytes
    key_id: str
    expires_at: datetime | None = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def __repr__(self) -> str:
        return f"EncryptedToken(key_id={self.key_id!r}, len={len(self.ciphertext)})"


@dataclass(frozen=True, slots=True)
class OAuthCredentials:
    access_token: EncryptedToken
    refresh_token: EncryptedToken | None
    scopes: tuple[str, ...]
    account_id: str
    account_handle: str | None = None
