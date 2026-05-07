from app.core.secrets import build_token_vault


def test_vault_round_trips():
    vault = build_token_vault()
    encrypted = vault.encrypt("super-secret-oauth-token")
    assert b"super-secret-oauth-token" not in encrypted.ciphertext
    assert vault.decrypt(encrypted) == "super-secret-oauth-token"
    # __repr__ must not leak ciphertext
    assert "super-secret" not in repr(encrypted)
