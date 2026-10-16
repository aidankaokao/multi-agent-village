"""問答與村民清單 API。"""

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from village.security.auth import CurrentUser, require_user

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(require_user)])


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


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
    run_id = uuid.uuid4().hex[:12]
    message = body.message.strip()
    await ctx.trace.start(run_id, message, me.id)  # 先建立紀錄：回傳 run_id 後馬上就查得到
    ctx.bus.claim(run_id, me.id)  # 在 task 開始前就認領，第一個事件也只送給提問者
    task = asyncio.create_task(ctx.runner.run(run_id, message, traced=True, user_id=me.id))
    ctx.owners[run_id] = me.id
    ctx.tasks.add(task)  # 保留參考，避免 task 被 GC
    ctx.active[run_id] = task
    task.add_done_callback(ctx.tasks.discard)
    task.add_done_callback(lambda _: (ctx.active.pop(run_id, None), ctx.owners.pop(run_id, None)))
    return {"run_id": run_id}


@router.post("/chat/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel(run_id: str, request: Request, me: CurrentUser = Depends(require_user)):
    """停止執行中的問答：正在等的 LLM 呼叫會被中斷，已完成的步驟保留在執行紀錄。只有提問者或管理員可以停。"""
    ctx = request.app.state.ctx
    task = ctx.active.get(run_id)
    if task is None or task.done() or (ctx.owners.get(run_id) != me.id and not me.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "這次問答已經結束或不存在")
    task.cancel()
    return {"ok": True}
