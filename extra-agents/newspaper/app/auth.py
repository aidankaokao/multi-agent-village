"""存取 token：呼叫端要在 header 帶 Authorization: Bearer <token>。

沒設定任何 token 時一律拒絕（不在沒有防護的狀態下對外開放）；比對用固定時間比較，避免從回應時間猜出 token。
"""

import hmac
import secrets

from fastapi import HTTPException, Request


def new_token() -> str:
    return secrets.token_urlsafe(32)


def check(request: Request, tokens: list[str]) -> None:
    if not tokens:
        raise HTTPException(503, "這個 agent 還沒設定存取 token（.env 的 NP_ACCESS_TOKENS），暫時不接受呼叫")
    header = request.headers.get("authorization", "")
    given = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not given or not any(hmac.compare_digest(given.encode(), t.encode()) for t in tokens):
        raise HTTPException(401, "存取 token 不對或沒有帶（Authorization: Bearer <token>）", headers={"WWW-Authenticate": "Bearer"})
