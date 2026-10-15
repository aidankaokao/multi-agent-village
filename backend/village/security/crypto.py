"""機密資料（API key）加解密。

- 演算法：AES-256-GCM，每次加密使用新的隨機 12-byte nonce
- AAD：呼叫端傳入（例如 "llm_provider:{id}:api_key"），把密文綁定到特定資料列與欄位
- 主金鑰（KEK）以 keyring 形式管理，支援多版本以便輪替：
    {"current": 2, "keys": {"1": "<base64>", "2": "<base64>"}}
- 來源優先順序：
    1. 環境變數 VILLAGE_MASTER_KEYS（keyring JSON）
    2. 環境變數 VILLAGE_MASTER_KEY（單一 base64 金鑰，視為 v1）
    3. <config_dir>/master.key（keyring JSON，權限 600）；不存在時自動產生
"""

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12
KEYRING_FILENAME = "master.key"


class DecryptionError(Exception):
    """解密失敗：金鑰版本不存在、AAD 不符或密文被竄改。"""


@dataclass
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class Keyring:
    def __init__(self, keys: dict[int, bytes], current: int):
        if current not in keys:
            raise ValueError(f"current key version {current} not in keyring")
        for v, k in keys.items():
            if len(k) != KEY_BYTES:
                raise ValueError(f"key v{v} must be {KEY_BYTES} bytes")
        self.keys = dict(keys)
        self.current = current

    @classmethod
    def generate(cls) -> "Keyring":
        return cls({1: AESGCM.generate_key(bit_length=256)}, current=1)

    def add_new_version(self) -> int:
        version = max(self.keys) + 1
        self.keys[version] = AESGCM.generate_key(bit_length=256)
        self.current = version
        return version

    def to_json(self) -> str:
        return json.dumps(
            {
                "current": self.current,
                "keys": {str(v): base64.b64encode(k).decode() for v, k in self.keys.items()},
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "Keyring":
        data = json.loads(raw)
        keys = {int(v): base64.b64decode(k) for v, k in data["keys"].items()}
        return cls(keys, current=int(data["current"]))


class SecretBox:
    def __init__(self, keyring: Keyring):
        self.keyring = keyring

    def encrypt(self, plaintext: str, aad: str) -> EncryptedSecret:
        version = self.keyring.current
        nonce = os.urandom(NONCE_BYTES)
        ct = AESGCM(self.keyring.keys[version]).encrypt(nonce, plaintext.encode(), aad.encode())
        return EncryptedSecret(ciphertext=ct, nonce=nonce, key_version=version)

    def decrypt(self, ciphertext: bytes, nonce: bytes, key_version: int, aad: str) -> str:
        key = self.keyring.keys.get(key_version)
        if key is None:
            raise DecryptionError(f"unknown key version v{key_version}")
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, aad.encode()).decode()
        except InvalidTag as e:
            raise DecryptionError("ciphertext/AAD mismatch") from e


# ---------------------------------------------------------------- keyring 載入 / 儲存


def keyring_path(config_dir: Path) -> Path:
    return config_dir / KEYRING_FILENAME


def keyring_from_env() -> Keyring | None:
    if raw := os.environ.get("VILLAGE_MASTER_KEYS"):
        return Keyring.from_json(raw)
    if raw := os.environ.get("VILLAGE_MASTER_KEY"):
        return Keyring({1: base64.b64decode(raw)}, current=1)
    return None


def save_keyring(path: Path, keyring: Keyring) -> None:
    """以 600 權限原子寫入（先寫暫存檔再 rename），避免寫到一半損毀金鑰。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(keyring.to_json())
    os.replace(tmp, path)


def load_keyring(config_dir: Path, *, create: bool = True) -> Keyring:
    if (kr := keyring_from_env()) is not None:
        return kr
    path = keyring_path(config_dir)
    if path.exists():
        return Keyring.from_json(path.read_text())
    if not create:
        raise FileNotFoundError(f"master keyring not found: {path}")
    kr = Keyring.generate()
    save_keyring(path, kr)
    return kr


def secret_hint(secret: str) -> str:
    """給 UI 顯示用的遮罩，例如 sk-…a1b2。短字串完全遮蔽。"""
    if len(secret) < 12:
        return "••••"
    return f"{secret[:3]}…{secret[-4:]}"
