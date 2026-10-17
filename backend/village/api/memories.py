"""長期記憶 API：自己的記憶列表、手動新增、修改、停用、刪除、全部清除（只看得到自己的）。"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete

from village.security.auth import CurrentUser, require_user
from village.store.models import Memory

router = APIRouter(prefix="/api/memories", tags=["memories"])
Me = Depends(require_user)


class MemoryOut(BaseModel):
    id: str
    content: str
    kind: str
    source: str
    enabled: bool
    use_count: int
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def of(cls, m: Memory) -> "MemoryOut":
        return cls(**{k: getattr(m, k) for k in cls.model_fields})


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=300)
    kind: Literal["preference", "fact"] = "fact"


class MemoryPatch(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=300)
    kind: Literal["preference", "fact"] | None = None
    enabled: bool | None = None


async def _own(s, memory_id: str, me: CurrentUser) -> Memory:
    m = await s.get(Memory, memory_id)
    if m is None or m.user_id != me.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這條記憶")
    return m


@router.get("")
async def list_memories(request: Request, me: CurrentUser = Me) -> list[MemoryOut]:
    return [MemoryOut.of(m) for m in reversed(await request.app.state.ctx.memories.all(me.id))]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_memory(body: MemoryCreate, request: Request, me: CurrentUser = Me) -> MemoryOut:
    m = await request.app.state.ctx.memories.add(me.id, body.content, body.kind, "manual")
    if m is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "已經有差不多的記憶了")
    return MemoryOut.of(m)


@router.patch("/{memory_id}")
async def update_memory(memory_id: str, body: MemoryPatch, request: Request, me: CurrentUser = Me) -> MemoryOut:
    async with request.app.state.ctx.sessionmaker() as s:
        m = await _own(s, memory_id, me)
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(m, k, " ".join(v.split()) if k == "content" else v)
        await s.commit()
        return MemoryOut.of(m)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, request: Request, me: CurrentUser = Me) -> None:
    async with request.app.state.ctx.sessionmaker() as s:
        await s.delete(await _own(s, memory_id, me))
        await s.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_memories(request: Request, me: CurrentUser = Me) -> None:
    async with request.app.state.ctx.sessionmaker() as s:
        await s.execute(delete(Memory).where(Memory.user_id == me.id))
        await s.commit()
