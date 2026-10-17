"""對話紀錄 API：自己的對話列表、讀取訊息、改名、刪除（只看得到自己的）。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from village.security.auth import CurrentUser, require_user
from village.store.models import Conversation, Message

router = APIRouter(prefix="/api/conversations", tags=["conversations"])
Me = Depends(require_user)


class ConversationOut(BaseModel):
    id: str
    title: str
    messages: int
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    run_id: str | None
    status: str
    meta: dict | None
    created_at: datetime


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


async def _own(s, conversation_id: str, me: CurrentUser) -> Conversation:
    c = await s.get(Conversation, conversation_id)
    if c is None or c.user_id != me.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這段對話")
    return c


@router.get("")
async def list_conversations(request: Request, me: CurrentUser = Me) -> list[ConversationOut]:
    counts = select(Message.conversation_id, func.count().label("n")).group_by(Message.conversation_id).subquery()
    stmt = (
        select(Conversation, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .where(Conversation.user_id == me.id)
        .order_by(Conversation.updated_at.desc())
        .limit(200)
    )
    async with request.app.state.ctx.sessionmaker() as s:
        return [
            ConversationOut(id=c.id, title=c.title, messages=n, created_at=c.created_at, updated_at=c.updated_at)
            for c, n in (await s.execute(stmt)).all()
        ]


@router.get("/{conversation_id}/messages")
async def get_messages(conversation_id: str, request: Request, me: CurrentUser = Me) -> list[MessageOut]:
    async with request.app.state.ctx.sessionmaker() as s:
        await _own(s, conversation_id, me)
        rows = await s.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
        )
        return [MessageOut(**{k: getattr(m, k) for k in MessageOut.model_fields}) for m in rows]


@router.patch("/{conversation_id}")
async def rename(conversation_id: str, body: RenameBody, request: Request, me: CurrentUser = Me) -> dict:
    async with request.app.state.ctx.sessionmaker() as s:
        c = await _own(s, conversation_id, me)
        c.title = body.title.strip()
        await s.commit()
        return {"id": c.id, "title": c.title}


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, request: Request, me: CurrentUser = Me) -> None:
    """刪除對話與訊息（執行紀錄另外保留，可在「紀錄」刪除）。"""
    async with request.app.state.ctx.sessionmaker() as s:
        c = await _own(s, conversation_id, me)
        await s.delete(c)
        await s.commit()
