"""Admin token：帳號系統（Phase 5）完成前，保護設定類 API。

來源：環境變數 VILLAGE_ADMIN_TOKEN，否則 <config_dir>/admin.token（不存在則產生，權限 600）。
"""

import hmac
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException, Request, status

ADMIN_TOKEN_FILENAME = "admin.token"


def load_admin_token(config_dir: Path) -> str:
    if token := os.environ.get("VILLAGE_ADMIN_TOKEN"):
        return token
    path = config_dir / ADMIN_TOKEN_FILENAME
    if path.exists():
        return path.read_text().strip()
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


async def require_admin(request: Request, authorization: str | None = Header(default=None)) -> None:
    expected: str = request.app.state.ctx.admin_token
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not given or not hmac.compare_digest(given, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin token required")
