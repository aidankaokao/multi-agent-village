"""駐村專家管理 API（管理員）：讀取名片、邀請入村、改網址 / token / 共享 LLM 開關、重新讀取名片、健康檢查。

駐村專家也是村民（agents 表，kind=remote），所以改名字、外觀、住處、停用、刪除沿用村民 API（/api/villagers）。
存取 token 以 AES-256-GCM 加密存在 remote_agents（AAD 綁定這位專家），API 只回傳遮罩，不回傳明文。
"""

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select

from village.api.villagers import _changed, _get, parse_spec
from village.core import remote
from village.core.docfiles import accepts_from_card
from village.core.registry import RESERVED_IDS
from village.security.auth import require_admin
from village.security.crypto import secret_hint
from village.store.models import AgentDef, RemoteAgent

router = APIRouter(prefix="/api/residents", tags=["residents"], dependencies=[Depends(require_admin)])
log = logging.getLogger("village.residents")


class PreviewBody(BaseModel):
    url: str = Field(min_length=1, max_length=500)


class InviteBody(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=40)
    url: str = Field(min_length=1, max_length=500)
    token: SecretStr | None = None
    name: str | None = Field(default=None, max_length=40)
    sprite: str | None = None
    share_llm: bool = False


class UpdateBody(BaseModel):
    url: str | None = Field(default=None, max_length=500)
    token: SecretStr | None = None  # 不傳 = 保留原值
    clear_token: bool = False
    share_llm: bool | None = None


class ResidentOut(BaseModel):
    id: str
    url: str
    a2a_path: str
    share_llm: bool
    has_token: bool
    token_hint: str | None
    status: str
    checked_at: datetime | None
    last_error: str | None
    card: dict | None
    card_fetched_at: datetime | None


def _ctx(request: Request):
    return request.app.state.ctx


def _out(row: AgentDef, ra: RemoteAgent) -> ResidentOut:
    r = row.spec.get("remote") or {}
    return ResidentOut(
        id=row.id, url=r.get("url", ""), a2a_path=r.get("a2a_path", "/a2a"), share_llm=bool(r.get("share_llm")),
        has_token=ra.token_ct is not None, token_hint=ra.token_hint, status=ra.status, checked_at=ra.checked_at,
        last_error=ra.last_error, card=ra.card, card_fetched_at=ra.card_fetched_at,
    )


def _set_token(request: Request, ra: RemoteAgent, token: str) -> None:
    enc = _ctx(request).box.encrypt(token, ra.token_aad)
    ra.token_ct, ra.token_nonce, ra.key_version, ra.token_hint = enc.ciphertext, enc.nonce, enc.key_version, secret_hint(token)


async def _resident(s, agent_id: str) -> tuple[AgentDef, RemoteAgent]:
    row = await _get(s, agent_id)
    ra = await s.get(RemoteAgent, agent_id)
    if row.spec.get("kind") != "remote" or ra is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"「{agent_id}」不是駐村專家")
    return row, ra


async def _apply_health(ctx, ra: RemoteAgent, base: str) -> bool:
    """檢查並寫回狀態；回傳狀態有沒有改變。"""
    ok, err = await remote.check_health(base)
    new = "online" if ok else "offline"
    changed = new != ra.status
    ra.status, ra.last_error, ra.checked_at = new, err, datetime.now(UTC)
    if changed:
        await ctx.bus.publish("residents.status", agent=ra.agent_id, status=new)
    return changed


def _fetch_error(e: remote.RemoteError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.post("/preview")
async def preview(body: PreviewBody) -> dict:
    """讀取對方的 agent card，給邀請表單預覽（還不會存）。"""
    try:
        base, card = await remote.fetch_card(body.url)
    except remote.RemoteError as e:
        raise _fetch_error(e) from None
    ok, err = await remote.check_health(base)
    return {
        "url": base, "card": card, "a2a_path": remote.a2a_path(card), "suggested_id": remote.suggest_id(card, base),
        "online": ok, "health_error": err, "wants_llm": bool(card.get("x-llm-headers")),
        "accepts": accepts_from_card(card),
    }


@router.get("")
async def list_residents(request: Request) -> list[ResidentOut]:
    async with _ctx(request).sessionmaker() as s:
        rows = (await s.execute(
            select(AgentDef, RemoteAgent).join(RemoteAgent, RemoteAgent.agent_id == AgentDef.id).where(~AgentDef.deleted)
        )).all()
        return [_out(a, r) for a, r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def invite(body: InviteBody, request: Request) -> ResidentOut:
    ctx = _ctx(request)
    if body.id in RESERVED_IDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"「{body.id}」是保留名稱")
    try:
        base, card = await remote.fetch_card(body.url)
    except remote.RemoteError as e:
        raise _fetch_error(e) from None
    spec = parse_spec(
        remote.spec_from_card(card, body.id, base, name=body.name, sprite=body.sprite, share_llm=body.share_llm).to_doc()
    )
    async with ctx.sessionmaker() as s:
        existing = await s.get(AgentDef, body.id)
        if existing is not None and not existing.deleted:
            raise HTTPException(status.HTTP_409_CONFLICT, f"「{body.id}」已經有人用了，換一個 id")
        if existing is not None:
            await s.delete(existing)  # 刪掉的內建範例 id 被重新使用
            await s.flush()
        row = AgentDef(id=body.id, spec=spec.to_doc())
        ra = RemoteAgent(agent_id=body.id, card=card, card_fetched_at=datetime.now(UTC), status="unknown")
        if body.token and body.token.get_secret_value():
            _set_token(request, ra, body.token.get_secret_value())
        s.add_all([row, ra])
        await s.flush()
        await _apply_health(ctx, ra, base)
        await s.commit()
        await s.refresh(row)
        out = _out(row, ra)
    await _changed(request)
    return out


@router.patch("/{agent_id}")
async def update(agent_id: str, body: UpdateBody, request: Request) -> ResidentOut:
    ctx = _ctx(request)
    async with ctx.sessionmaker() as s:
        row, ra = await _resident(s, agent_id)
        spec = dict(row.spec)
        r = dict(spec["remote"])
        if body.url is not None:
            try:
                r["url"], _ = remote.split_url(body.url)
            except remote.RemoteError as e:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
        if body.share_llm is not None:
            r["share_llm"] = body.share_llm
        spec["remote"] = r
        row.spec = parse_spec(spec).to_doc()
        if body.clear_token:
            ra.token_ct = ra.token_nonce = ra.key_version = ra.token_hint = None
        elif body.token and body.token.get_secret_value():
            _set_token(request, ra, body.token.get_secret_value())
        if body.url is not None:
            await _apply_health(ctx, ra, r["url"])
        await s.commit()
        await s.refresh(row)
        out = _out(row, ra)
    await _changed(request)
    return out


@router.post("/{agent_id}/refresh")
async def refresh_card(agent_id: str, request: Request) -> ResidentOut:
    """重新讀取名片：更新說明、技能、A2A 路徑；名字、外觀、住處、關鍵字、共享開關保留管理員的設定。"""
    ctx = _ctx(request)
    async with ctx.sessionmaker() as s:
        row, ra = await _resident(s, agent_id)
        old = row.spec
        try:
            base, card = await remote.fetch_card(old["remote"]["url"])
        except remote.RemoteError as e:
            raise _fetch_error(e) from None
        v = old.get("x-village") or {}
        spec = remote.spec_from_card(
            card, agent_id, base, name=old["card"]["name"], sprite=v.get("sprite"), home=v.get("home"),
            keywords=v.get("keywords"), share_llm=bool(old["remote"].get("share_llm")),
        )
        row.spec = parse_spec(spec.to_doc()).to_doc()
        ra.card, ra.card_fetched_at = card, datetime.now(UTC)
        await _apply_health(ctx, ra, base)
        await s.commit()
        await s.refresh(row)
        out = _out(row, ra)
    await _changed(request)
    return out


@router.post("/{agent_id}/check")
async def check_now(agent_id: str, request: Request) -> ResidentOut:
    ctx = _ctx(request)
    async with ctx.sessionmaker() as s:
        row, ra = await _resident(s, agent_id)
        await _apply_health(ctx, ra, row.spec["remote"]["url"])
        await s.commit()
        return _out(row, ra)


# ---------------------------------------------------------------- 定期健康檢查（main.py 啟動）

CHECK_EVERY_S = 60


async def check_all(ctx) -> None:
    async with ctx.sessionmaker() as s:
        rows = (await s.execute(
            select(AgentDef, RemoteAgent).join(RemoteAgent, RemoteAgent.agent_id == AgentDef.id)
            .where(~AgentDef.deleted, AgentDef.enabled)
        )).all()
        await asyncio.gather(*(_apply_health(ctx, ra, a.spec["remote"]["url"]) for a, ra in rows))
        await s.commit()


async def health_loop(ctx) -> None:
    while True:
        try:
            await check_all(ctx)
        except Exception:
            log.exception("駐村專家健康檢查失敗")
        await asyncio.sleep(CHECK_EVERY_S)
