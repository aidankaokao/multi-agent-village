"""登入、註冊、登出、改密碼（不需要先登入），以及管理員的帳號管理。"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from village.security import auth
from village.security.auth import CurrentUser, require_admin, require_user
from village.store.models import AuthSession, User

router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])


class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class RegisterBody(Credentials):
    display_name: str = Field(default="", max_length=100)


class PasswordBody(BaseModel):
    old_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


async def _count_users(s) -> int:
    return await s.scalar(select(func.count()).select_from(User)) or 0


@router.get("/status")
async def auth_status(request: Request, response: Response) -> dict:
    """登入頁用：是否還沒有任何帳號（第一位註冊的人成為管理員）、能不能自己註冊、目前登入的是誰。"""
    ctx = request.app.state.ctx
    async with ctx.sessionmaker() as s:
        n = await _count_users(s)
    user = await auth.current_user(request, response)
    return {
        "setup_required": n == 0,
        "allow_registration": n == 0 or ctx.rules.current.allow_registration,
        "user": user.to_dict() if user else None,
        "llm_ready": bool(user) and await ctx.llm.has_models(user.id),  # 還沒設定自己的 LLM → 前端提醒
    }


@router.post("/register")
async def register(body: RegisterBody, request: Request, response: Response) -> dict:
    """註冊並登入。系統還沒有帳號時，這個帳號就是管理員。"""
    ctx = request.app.state.ctx
    username = auth.check_username(body.username)
    auth.check_password(body.password)
    async with ctx.sessionmaker() as s:
        first = await _count_users(s) == 0
        if not first and not ctx.rules.current.allow_registration:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "目前不開放註冊，請找管理員幫你建立帳號")
        if await s.scalar(select(User).where(User.username == username)):
            raise HTTPException(status.HTTP_409_CONFLICT, "帳號：這個帳號已經有人用了")
        user = User(
            username=username, display_name=body.display_name.strip() or username,
            password_hash=auth.hash_password(body.password), role="admin" if first else "member",
        )
        s.add(user)
        await s.commit()
    return {"user": (await auth.login(request, response, user)).to_dict(), "first": first}


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response) -> dict:
    username = body.username.strip().lower()
    keys = (f"u:{username}", f"ip:{auth.client_ip(request)}")
    auth.throttle.check(*keys)
    async with request.app.state.ctx.sessionmaker() as s:
        user = await s.scalar(select(User).where(User.username == username))
    ok = auth.verify_password(body.password, user.password_hash if user else auth._DUMMY_HASH)
    if not user or not ok:
        auth.throttle.fail(*keys)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "帳號或密碼不正確")
    if not user.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "這個帳號已被停用，請聯絡管理員")
    auth.throttle.reset(*keys)
    return {"user": (await auth.login(request, response, user)).to_dict()}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    await auth.logout(request, response)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordBody, request: Request, me: CurrentUser = Depends(require_user)) -> None:
    auth.check_password(body.new_password)
    async with request.app.state.ctx.sessionmaker() as s:
        user = await s.get(User, me.id)
        if not auth.verify_password(body.old_password, user.password_hash):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "目前的密碼不正確")
        user.password_hash = auth.hash_password(body.new_password)
        await s.commit()


class DeleteMeBody(BaseModel):
    password: str = Field(max_length=200)


@router.post("/delete-account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_account(
    body: DeleteMeBody, request: Request, response: Response, me: CurrentUser = Depends(require_user)
) -> None:
    """刪除自己的帳號（要輸入密碼確認）。自己的 LLM 設定一併刪除；執行紀錄保留但不再標示是誰。"""
    async with request.app.state.ctx.sessionmaker() as s:
        user = await s.get(User, me.id)
        if not auth.verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "密碼不正確")
        if user.role == "admin" and await _other_admins(s, user.id) == 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "你是唯一的管理員：請先把另一個帳號設為管理員，再刪除自己的帳號"
            )
        await s.delete(user)
        await s.commit()
    response.delete_cookie(auth.COOKIE, path="/")


# ---------------------------------------------------------------- 帳號管理（管理員）


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    enabled: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def of(cls, u: User) -> "UserOut":
        return cls(**{k: getattr(u, k) for k in cls.model_fields})


class UserCreate(RegisterBody):
    role: Literal["admin", "member"] = "member"


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    role: Literal["admin", "member"] | None = None
    enabled: bool | None = None
    password: str | None = Field(default=None, max_length=200)  # 管理員幫忙重設


async def _other_admins(s, user_id: str) -> int:
    return await s.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.enabled, User.id != user_id)
    ) or 0


@users_router.get("")
async def list_users(request: Request) -> list[UserOut]:
    async with request.app.state.ctx.sessionmaker() as s:
        return [UserOut.of(u) for u in await s.scalars(select(User).order_by(User.created_at))]


@users_router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, request: Request) -> UserOut:
    username = auth.check_username(body.username)
    auth.check_password(body.password)
    async with request.app.state.ctx.sessionmaker() as s:
        if await s.scalar(select(User).where(User.username == username)):
            raise HTTPException(status.HTTP_409_CONFLICT, "帳號：這個帳號已經有人用了")
        user = User(
            username=username, display_name=body.display_name.strip() or username,
            password_hash=auth.hash_password(body.password), role=body.role,
        )
        s.add(user)
        await s.commit()
        return UserOut.of(user)


@users_router.patch("/{user_id}")
async def update_user(user_id: str, body: UserPatch, request: Request) -> UserOut:
    async with request.app.state.ctx.sessionmaker() as s:
        user = await s.get(User, user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這個帳號")
        losing_admin = user.role == "admin" and (body.role == "member" or body.enabled is False)
        if losing_admin and await _other_admins(s, user.id) == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "至少要保留一位啟用中的管理員")
        if body.display_name is not None:
            user.display_name = body.display_name.strip() or user.username
        if body.role is not None:
            user.role = body.role
        if body.enabled is not None:
            user.enabled = body.enabled
        if body.password:
            auth.check_password(body.password)
            user.password_hash = auth.hash_password(body.password)
        if body.enabled is False or body.password:  # 停用或重設密碼：登出這個人所有裝置
            await s.execute(AuthSession.__table__.delete().where(AuthSession.user_id == user.id))
        await s.commit()
        return UserOut.of(user)


@users_router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, request: Request, me: CurrentUser = Depends(require_admin)) -> None:
    if user_id == me.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "不能刪除自己的帳號")
    async with request.app.state.ctx.sessionmaker() as s:
        user = await s.get(User, user_id)
        if user is None:
            return
        if user.role == "admin" and await _other_admins(s, user.id) == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "至少要保留一位啟用中的管理員")
        await s.delete(user)
        await s.commit()
