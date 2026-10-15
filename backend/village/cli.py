"""village CLI

  village serve          啟動後端（預設 127.0.0.1:8000）
  village rotate-key     產生新主金鑰版本並重新加密所有 API key
"""

import argparse
import asyncio

from sqlalchemy import select

from village.config import Settings
from village.security.crypto import SecretBox, keyring_from_env, keyring_path, load_keyring, save_keyring
from village.store.db import init_db, make_engine, make_sessionmaker
from village.store.models import LlmProvider


async def rotate_key(settings: Settings) -> None:
    if keyring_from_env() is not None:
        raise SystemExit("主金鑰來自環境變數，請在 secret manager 端輪替後再執行重新加密。")
    keyring = load_keyring(settings.config_dir, create=False)
    new_version = keyring.add_new_version()
    # 先存新 keyring（保留舊版本），再重新加密；中途失敗也還能用舊金鑰解密
    save_keyring(keyring_path(settings.config_dir), keyring)
    box = SecretBox(keyring)  # keyring 含所有版本：用舊版解密、用 current 加密

    engine = make_engine(settings.database_url)
    await init_db(engine)
    sm = make_sessionmaker(engine)
    count = 0
    async with sm() as s:
        for p in (await s.execute(select(LlmProvider).where(LlmProvider.api_key_ct.is_not(None)))).scalars():
            plain = box.decrypt(p.api_key_ct, p.api_key_nonce, p.key_version, p.api_key_aad)
            enc = box.encrypt(plain, p.api_key_aad)
            p.api_key_ct, p.api_key_nonce, p.key_version = enc.ciphertext, enc.nonce, enc.key_version
            count += 1
        await s.commit()
    await engine.dispose()
    print(f"已輪替到 v{new_version}，重新加密 {count} 筆 API key。舊金鑰版本仍保留在 keyring 中。")


def main() -> None:
    parser = argparse.ArgumentParser(prog="village")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("rotate-key")
    args = parser.parse_args()
    settings = Settings.from_env()

    if args.cmd == "serve":
        from village.main import run

        run()
    elif args.cmd == "rotate-key":
        asyncio.run(rotate_key(settings))
