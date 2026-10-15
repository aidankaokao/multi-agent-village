import os
import stat

import pytest

from village.security.crypto import DecryptionError, Keyring, SecretBox, load_keyring, secret_hint
from village.security.redact import redact, register_secret

AAD = "llm_provider:abc:api_key"


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    monkeypatch.delenv("VILLAGE_MASTER_KEYS", raising=False)
    monkeypatch.delenv("VILLAGE_MASTER_KEY", raising=False)


def test_roundtrip_and_fresh_nonce():
    box = SecretBox(Keyring.generate())
    a = box.encrypt("sk-secret-value-123", AAD)
    b = box.encrypt("sk-secret-value-123", AAD)
    assert a.nonce != b.nonce and a.ciphertext != b.ciphertext
    assert b"sk-secret" not in a.ciphertext
    assert box.decrypt(a.ciphertext, a.nonce, a.key_version, AAD) == "sk-secret-value-123"


def test_wrong_aad_rejected():
    box = SecretBox(Keyring.generate())
    e = box.encrypt("sk-secret-value-123", AAD)
    with pytest.raises(DecryptionError):
        box.decrypt(e.ciphertext, e.nonce, e.key_version, "llm_provider:other:api_key")


def test_tampered_ciphertext_rejected():
    box = SecretBox(Keyring.generate())
    e = box.encrypt("sk-secret-value-123", AAD)
    tampered = bytes([e.ciphertext[0] ^ 1]) + e.ciphertext[1:]
    with pytest.raises(DecryptionError):
        box.decrypt(tampered, e.nonce, e.key_version, AAD)


def test_other_master_key_cannot_decrypt():
    e = SecretBox(Keyring.generate()).encrypt("sk-secret-value-123", AAD)
    with pytest.raises(DecryptionError):
        SecretBox(Keyring.generate()).decrypt(e.ciphertext, e.nonce, e.key_version, AAD)


def test_rotation_keeps_old_versions_decryptable():
    kr = Keyring.generate()
    box = SecretBox(kr)
    old = box.encrypt("sk-secret-value-123", AAD)
    assert kr.add_new_version() == 2
    new = box.encrypt("sk-secret-value-123", AAD)
    assert new.key_version == 2
    assert box.decrypt(old.ciphertext, old.nonce, 1, AAD) == "sk-secret-value-123"
    assert box.decrypt(new.ciphertext, new.nonce, 2, AAD) == "sk-secret-value-123"


def test_keyring_file_created_with_0600_and_reloaded(tmp_path):
    kr = load_keyring(tmp_path / "cfg")
    path = tmp_path / "cfg" / "master.key"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert load_keyring(tmp_path / "cfg").keys == kr.keys


def test_env_key_takes_precedence(tmp_path, monkeypatch):
    kr = Keyring.generate()
    monkeypatch.setenv("VILLAGE_MASTER_KEYS", kr.to_json())
    assert load_keyring(tmp_path).keys == kr.keys
    assert not (tmp_path / "master.key").exists()


def test_hint_and_redaction():
    assert secret_hint("sk-abcdefghijkl1234") == "sk-…1234"
    assert secret_hint("short") == "••••"
    register_secret("my-custom-token-xyz")
    out = redact("failed with my-custom-token-xyz and Authorization: Bearer abcdefghijklmnop and sk-proj-abcdefgh123")
    assert "my-custom-token-xyz" not in out
    assert "abcdefghijklmnop" not in out
    assert "sk-proj-abcdefgh123" not in out
