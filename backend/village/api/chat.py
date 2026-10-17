"""問答與村民清單 API。"""

import asyncio
import base64
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from village.api import images as image_api
from village.core import appraiser, scribe
from village.core.context import refers_back
from village.core.painter import PaintEnv, Reference
from village.llm.base import ImagePart
from village.llm.service import NO_VISION, NOT_CONFIGURED, llm_user
from village.security.auth import CurrentUser, require_user

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(require_user)])
log = logging.getLogger("village.chat")


DEFAULT_IMAGE_QUESTION = "這張圖片裡有什麼？"

# 追問時像在「修改上一張畫」：改、調、換、加上、去掉、再…一點、背景、顏色、風格…
_EDIT = re.compile(r"改|調|換|修|加上|加一|去掉|拿掉|刪掉|移除|再.{0,6}(一點|一些|些)|背景|顏色|色調|風格|畫風|更亮|更暗|放大|縮小")


class ChatBody(BaseModel):
    message: str = Field(default="", max_length=2000)  # 有附圖時可以留空
    conversation_id: str | None = None  # 接著哪段對話；不填 = 開新對話
    images: list[image_api.ImageIn] = Field(default_factory=list, max_length=image_api.HARD_MAX_IMAGES)


class AgentOut(BaseModel):
    id: str
    name: str
    description: str
    kind: str
    sprite: str | None
    home: tuple[int, int] | None
    skills: list[dict]
    tags: list[str]


@router.get("/agents")
async def list_agents(request: Request):
    registry = request.app.state.ctx.registry
    agents = [
        AgentOut(
            id=a.id,
            name=a.name,
            description=a.card.description,
            kind=a.kind,
            sprite=a.village.sprite,
            home=a.village.home,
            skills=[s.model_dump() for s in a.card.skills],
            tags=a.card.tags,
        )
        for a in registry.all()
    ]
    return {"agents": agents, "errors": registry.errors}


@router.post("/chat", status_code=status.HTTP_202_ACCEPTED)
async def chat(body: ChatBody, request: Request, me: CurrentUser = Depends(require_user)):
    """非同步執行：立即回傳 run_id，進度與結果透過 /ws 事件推送。"""
    ctx = request.app.state.ctx
    if not await ctx.llm.has_models(me.id):
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_CONFIGURED)  # 用自己的 LLM：還沒設定就先提醒
    rules = ctx.rules.current
    message = body.message.strip()
    images = image_api.decode(body.images, rules.max_images)
    if not message and not images:
        raise HTTPException(422, "請輸入問題")
    if images and not await ctx.llm.has_vision(me.id):
        raise HTTPException(status.HTTP_409_CONFLICT, NO_VISION)  # 沒有視覺模型看不懂圖，先提醒
    message = message or DEFAULT_IMAGE_QUESTION
    run_id = uuid.uuid4().hex[:12]
    started = await ctx.conversations.start_turn(me.id, body.conversation_id, message, images)
    conv_id = started.conversation_id
    history = await ctx.conversations.recent_turns(conv_id, rules.context_turns)
    # 修改上一張畫作（「背景改成夜晚」）：畫家以那張畫為底，鑑定師不用去重看更早附的照片
    painting = None
    if not images and body.conversation_id and (_EDIT.search(message) or refers_back(message)):
        painting = await ctx.conversations.last_painting(conv_id, me.id)
    # 鑑定師要看的圖：這次附的；沒附但前幾輪有圖、而且問題像在問那張圖 → 重看原圖
    relook = False
    look = images
    if not look and not painting and body.conversation_id and appraiser.wants_relook(message):
        look = await ctx.conversations.recent_images(conv_id, rules.context_turns)
        relook = bool(look)
        if relook and not await ctx.llm.has_vision(me.id):
            look, relook = [], False  # 視覺模型被拿掉了：照一般問答處理
    parts = [ImagePart(mime, base64.b64encode(data).decode()) for mime, data in look]
    paint = PaintEnv(
        ctx.llm, ctx.conversations, me.id, conv_id, rules.paint_per_run, rules.paint_per_day,
        references=_references(images, painting, look),
    )
    memories = [m.content for m in await ctx.memories.relevant(me.id, message, rules.memory_max)] if rules.memory_enabled else []
    await ctx.trace.start(run_id, message, me.id)  # 先建立紀錄：回傳 run_id 後馬上就查得到
    ctx.bus.claim(run_id, me.id)  # 在 task 開始前就認領，第一個事件也只送給提問者

    async def job():
        """執行問答，結束（含失敗、取消）後把回答存回對話。"""
        try:
            out = await ctx.runner.run(
                run_id, message, traced=True, user_id=me.id, history=history, memories=memories,
                images=parts, relook=relook, paint=paint,
            )
        except asyncio.CancelledError:
            await ctx.conversations.finish_turn(conv_id, run_id, "", "cancelled")
            raise
        reviews = out.get("reviews") or []
        meta = {
            "attachments": out.get("attachments") or [],
            "agents": [st["agent"] for st in out.get("steps") or []],
            "review": reviews[-1].get("passed") if reviews else None,
        }
        status_ = "done" if out.get("ok") else "error"
        await ctx.conversations.finish_turn(conv_id, run_id, out.get("answer") or out.get("error") or "", status_, meta)
        if rules.memory_auto and out.get("ok"):
            await take_notes_after(ctx, me.id, run_id, message)
        return out

    task = asyncio.create_task(job())
    ctx.owners[run_id] = me.id
    ctx.tasks.add(task)  # 保留參考，避免 task 被 GC
    ctx.active[run_id] = task
    task.add_done_callback(ctx.tasks.discard)
    task.add_done_callback(lambda _: (ctx.active.pop(run_id, None), ctx.owners.pop(run_id, None)))
    return {"run_id": run_id, "conversation_id": conv_id, "image_ids": started.image_ids}


def _references(
    images: list[tuple[str, bytes]], painting: tuple[str, bytes] | None, look: list[tuple[str, bytes]]
) -> list[Reference]:
    """畫家可以當底圖的參考圖：這次附的圖 → 上一輪的畫作（像在說要修改它時）→ 鑑定師重看的前幾輪附圖。"""
    if images:
        return [Reference(m, d, "upload") for m, d in images]
    if painting:
        return [Reference(*painting, "painting")]
    return [Reference(m, d, "upload") for m, d in look]


async def take_notes_after(ctx, user_id: str, run_id: str, message: str) -> None:
    """問答結束後請書記記錄（背景進行，不影響回答）；新增 / 忘記的內容透過事件告訴提問者。"""
    if not scribe.worth_checking(message):
        return
    ctx.bus.claim(run_id, user_id)  # 書記的事件也只有提問者看得到內容
    await ctx.bus.publish("memory.started", run_id=run_id, agent=scribe.SCRIBE_ID)
    added: list[str] = []
    forgot: list[str] = []
    try:
        existing = [(m.id, m.content) for m in await ctx.memories.all(user_id)]
        with llm_user(user_id):
            notes = await scribe.take_notes(ctx.llm, message, existing)
        forgot = await ctx.memories.remove(user_id, notes.forget)
        for content, kind in notes.add:
            if await ctx.memories.add(user_id, content, kind, "auto", run_id):
                added.append(content)
    except Exception:  # 書記出錯不能影響問答
        log.exception("書記記錄失敗")
    finally:
        await ctx.bus.publish("memory.finished", run_id=run_id, agent=scribe.SCRIBE_ID, added=added, forgot=forgot)
        ctx.bus.release(run_id)


@router.post("/chat/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel(run_id: str, request: Request, me: CurrentUser = Depends(require_user)):
    """停止執行中的問答：正在等的 LLM 呼叫會被中斷，已完成的步驟保留在執行紀錄。只有提問者或管理員可以停。"""
    ctx = request.app.state.ctx
    task = ctx.active.get(run_id)
    if task is None or task.done() or (ctx.owners.get(run_id) != me.id and not me.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "這次問答已經結束或不存在")
    task.cancel()
    return {"ok": True}
