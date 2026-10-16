"""村民工坊 API：和工匠對話，產生村民設定草稿（不會直接儲存，交給前端編輯器確認）。"""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from village.core import workshop
from village.core.workshop import CRAFTSMAN_ID, Neighbor, SpriteOption
from village.llm.base import ChatMessage
from village.security.auth import require_admin
from village.store.models import AgentDef

router = APIRouter(prefix="/api/workshop", tags=["workshop"], dependencies=[Depends(require_admin)])


class Msg(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class SpriteIn(BaseModel):
    id: str
    name: str
    desc: str = ""


class WorkshopBody(BaseModel):
    messages: list[Msg] = Field(min_length=1, max_length=40)
    sprites: list[SpriteIn] = []  # 前端 manifest 裡可給村民用的外觀
    draft: dict | None = None  # 修改既有草稿時帶上
    target: str | None = None  # 'tier:large' / 'tier:small' / 模型 id；預設 large
    blocked: list[tuple[int, int]] = Field(default=[], max_length=600)  # 地圖上有裝飾的格子


class InterviewOut(BaseModel):
    reply: str
    ready: bool
    source: str


class GenerateOut(BaseModel):
    spec: dict
    notes: list[str]
    summary: str
    source: str
    model: str | None


async def _neighbors(request: Request) -> list[Neighbor]:
    async with request.app.state.ctx.sessionmaker() as s:
        rows = await s.scalars(select(AgentDef).where(~AgentDef.deleted))
        out = []
        for r in rows:
            card, v = r.spec.get("card", {}), r.spec.get("x-village", {})
            home = tuple(v["home"]) if v.get("home") else None
            out.append(Neighbor(id=r.id, name=card.get("name", r.id), description=card.get("description", ""), home=home))
        return out


async def _busy(request: Request, type_: str, **data) -> None:
    await request.app.state.ctx.bus.publish(type_, agent=CRAFTSMAN_ID, **data)


def _msgs(body: WorkshopBody) -> list[ChatMessage]:
    return [ChatMessage(m.role, m.content) for m in body.messages]


@router.post("/interview")
async def interview(body: WorkshopBody, request: Request) -> InterviewOut:
    await _busy(request, "workshop.started", stage="interview")
    try:
        r = await workshop.interview(
            request.app.state.ctx.llm, _msgs(body), await _neighbors(request),
            has_draft=body.draft is not None, target=body.target or "tier:large",
        )
    finally:
        await _busy(request, "workshop.finished", stage="interview")
    return InterviewOut(reply=r.reply, ready=r.ready, source=r.source)


@router.post("/generate")
async def generate(body: WorkshopBody, request: Request) -> GenerateOut:
    await _busy(request, "workshop.started", stage="generate")
    ok = False
    try:
        d = await workshop.generate(
            request.app.state.ctx.llm,
            _msgs(body),
            [SpriteOption(s.id, s.name, s.desc) for s in body.sprites],
            await _neighbors(request),
            draft=body.draft,
            target=body.target or "tier:large",
            blocked={tuple(b) for b in body.blocked},
        )
        ok = d.source == "llm"
    finally:
        await _busy(request, "workshop.finished" if ok else "workshop.error", stage="generate")
    return GenerateOut(spec=d.spec, notes=d.notes, summary=d.summary, source=d.source, model=d.model)
