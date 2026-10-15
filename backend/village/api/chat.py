"""問答與村民清單 API。"""

import asyncio
import uuid

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["chat"])


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
async def chat(body: ChatBody, request: Request):
    """非同步執行：立即回傳 run_id，進度與結果透過 /ws 事件推送。"""
    ctx = request.app.state.ctx
    run_id = uuid.uuid4().hex[:12]
    task = asyncio.create_task(ctx.runner.run(run_id, body.message.strip()))
    ctx.tasks.add(task)  # 保留參考，避免 task 被 GC
    task.add_done_callback(ctx.tasks.discard)
    return {"run_id": run_id}
