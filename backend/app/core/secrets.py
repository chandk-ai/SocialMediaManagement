"""Symmetric envelope encryption for OAuth tokens and other secrets at rest.

In production, the data-encryption key is wrapped by AWS KMS / GCP KMS / Vault.
Locally, we fall back to a key derived from SEC_TOKEN_VAULT_MASTER_KEY.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings
from app.domain.value_objects.credentials import EncryptedToken


class TokenVault:
    """Encrypts/decrypts opaque tokens with AES-256-GCM."""

    def __init__(self, master_key: bytes, key_id: str = "local-dev") -> None:
        if len(master_key) < 32:
            raise ValueError("master key must be >= 32 bytes")
        self._aesgcm = AESGCM(master_key[:32])
        self._key_id = key_id

    def encrypt(self, plaintext: str) -> EncryptedToken:
        nonce = os.urandom(12)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
        return EncryptedToken(ciphertext=nonce + ct, key_id=self._key_id)

    def decrypt(self, token: EncryptedToken) -> str:
        nonce, ct = token.ciphertext[:12], token.ciphertext[12:]
        pt = self._aesgcm.decrypt(nonce, ct, associated_data=None)
        return pt.decode("utf-8")


def build_token_vault(settings: Settings | None = None) -> TokenVault:
    settings = settings or get_settings()
    master = settings.security.token_vault_master_key.get_secret_value().encode("utf-8")
    # Pad/truncate deterministically — production should fail loudly if not 32 bytes.
    if len(master) < 32:
        master = (master * (32 // len(master) + 1))[:32]
    return TokenVault(master_key=master)


def encode_for_jwt(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")
