"""帳號與登入 session。

- 密碼：scrypt（標準庫，不需額外套件），格式 scrypt$n$r$p$salt_b64$hash_b64，比對用 hmac.compare_digest
- Session：登入時產生隨機 token 放進 httpOnly cookie（記住 30 天，使用中會自動延長）；
  DB 只存 token 的 SHA-256，DB 外洩也拿不到能登入的 token
- 權限：require_user（登入即可：問答、自己的紀錄）、require_admin（管理員：村民、村規、LLM 設定、帳號管理）
- 暴力破解：同一帳號或同一來源連續失敗 5 次，鎖 60 秒
"""

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, Response, WebSocket, status
from sqlalchemy import delete, select

from village.store.models import AuthSession, User

COOKIE = "village_session"
SESSION_DAYS = 30
RENEW_AFTER = timedelta(days=1)  # 剩不到 29 天就延長回 30 天
SCRYPT = (2**14, 8, 1)
USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
MIN_PASSWORD = 8
MAX_FAILS, LOCK_SECONDS = 5, 60


# ---------------------------------------------------------------- 密碼


def hash_password(password: str) -> str:
    n, r, p = SCRYPT
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


# 用來讓「帳號不存在」和「密碼錯」花一樣的時間，避免被拿來探測帳號
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def check_username(username: str) -> str:
    u = username.strip().lower()
    if not USERNAME.match(u):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "帳號：3～32 個字元，小寫英文、數字、底線、點或減號，開頭要是英文或數字")
    return u


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"密碼：至少 {MIN_PASSWORD} 個字元")
    if len(password) > 200:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "密碼：太長了（上限 200 字元）")


# ---------------------------------------------------------------- 失敗次數限制


class Throttle:
    def __init__(self):
        self._fails: dict[str, list[float]] = {}

    def check(self, *keys: str) -> None:
        now = time.monotonic()
        for k in keys:
            recent = [t for t in self._fails.get(k, []) if now - t < LOCK_SECONDS]
            self._fails[k] = recent
            if len(recent) >= MAX_FAILS:
                wait = int(LOCK_SECONDS - (now - recent[0])) + 1
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"嘗試太多次，請 {wait} 秒後再試")

    def fail(self, *keys: str) -> None:
        for k in keys:
            self._fails.setdefault(k, []).append(time.monotonic())

    def reset(self, *keys: str) -> None:
        for k in keys:
            self._fails.pop(k, None)


throttle = Throttle()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


# ---------------------------------------------------------------- session


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)  # SQLite 讀回來沒有時區


@dataclass
class CurrentUser:
    id: str
    username: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict:
        return {"id": self.id, "username": self.username, "display_name": self.display_name, "role": self.role}


def _set_cookie(response: Response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure, path="/"
    )


async def login(request: Request, response: Response, user: User) -> CurrentUser:
    token = secrets.token_urlsafe(32)
    async with request.app.state.ctx.sessionmaker() as s:
        s.add(AuthSession(
            token_hash=_digest(token), user_id=user.id, expires_at=_now() + timedelta(days=SESSION_DAYS),
            user_agent=(request.headers.get("user-agent") or "")[:300],
        ))
        db_user = await s.get(User, user.id)
        db_user.last_login_at = _now()
        await s.execute(delete(AuthSession).where(AuthSession.expires_at < _now()))  # 順便清掉過期的
        await s.commit()
    _set_cookie(response, request, token)
    return CurrentUser(user.id, user.username, user.display_name, user.role)


async def logout(request: Request, response: Response) -> None:
    if token := request.cookies.get(COOKIE):
        async with request.app.state.ctx.sessionmaker() as s:
            await s.execute(delete(AuthSession).where(AuthSession.token_hash == _digest(token)))
            await s.commit()
    response.delete_cookie(COOKIE, path="/")


async def _lookup(sessionmaker, token: str | None) -> tuple[CurrentUser, AuthSession] | None:
    if not token:
        return None
    async with sessionmaker() as s:
        row = (
            await s.execute(
                select(AuthSession, User).join(User, User.id == AuthSession.user_id).where(
                    AuthSession.token_hash == _digest(token)
                )
            )
        ).first()
    if row is None:
        return None
    sess, user = row
    if _aware(sess.expires_at) < _now() or not user.enabled:
        return None
    return CurrentUser(user.id, user.username, user.display_name, user.role), sess


async def current_user(request: Request, response: Response) -> CurrentUser | None:
    """沒登入回 None；有登入時順便延長 session（滑動到期）。"""
    if hasattr(request.state, "user"):
        return request.state.user
    token = request.cookies.get(COOKIE)
    found = await _lookup(request.app.state.ctx.sessionmaker, token)
    user = None
    if found:
        user, sess = found
        if _aware(sess.expires_at) - _now() < timedelta(days=SESSION_DAYS) - RENEW_AFTER:
            async with request.app.state.ctx.sessionmaker() as s:
                row = await s.get(AuthSession, sess.token_hash)
                if row:
                    row.expires_at = _now() + timedelta(days=SESSION_DAYS)
                    await s.commit()
            _set_cookie(response, request, token)  # type: ignore[arg-type]
    request.state.user = user
    return user


async def require_user(request: Request, response: Response) -> CurrentUser:
    user = await current_user(request, response)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "請先登入")
    return user


async def require_admin(request: Request, response: Response) -> CurrentUser:
    user = await require_user(request, response)
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理員權限")
    return user


async def websocket_user(ws: WebSocket) -> CurrentUser | None:
    found = await _lookup(ws.app.state.ctx.sessionmaker, ws.cookies.get(COOKIE))
    return found[0] if found else None
