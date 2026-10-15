"""Admin token（選用）：帳號系統（Phase 5）完成前，保護設定類 API。

- VILLAGE_ADMIN_TOKEN 未設定（預設）：不驗證，任何能連到網頁的人都能改 LLM 設定
- 有設定：設定類 API 需帶 Authorization: Bearer <token>
"""

import hmac
import os

from fastapi import Header, HTTPException, Request, status


def load_admin_token() -> str | None:
    return os.environ.get("VILLAGE_ADMIN_TOKEN") or None


async def require_admin(request: Request, authorization: str | None = Header(default=None)) -> None:
    expected: str | None = request.app.state.ctx.admin_token
    if expected is None:
        return
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not given or not hmac.compare_digest(given, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin token required")
