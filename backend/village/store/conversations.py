"""對話紀錄：每個帳號的對話與訊息。問答前讀最近幾輪當上下文，問答結束後把回答存回來。

附圖存在 chat_images（跟著對話保存），使用者訊息的 meta.images 記圖片 id。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from village.core.context import Turn
from village.store.models import ChatImage, Conversation, Message

TITLE_CHARS = 40
TURN_CHARS = 1500  # 每則訊息帶進上下文的上限，避免很長的回答把 prompt 撐爆


def _now() -> datetime:
    return datetime.now(UTC)


def _clip(text: str) -> str:
    return text if len(text) <= TURN_CHARS else text[:TURN_CHARS] + "…（後略）"


@dataclass
class Started:
    conversation_id: str
    image_ids: list[str] = field(default_factory=list)


class ConversationStore:
    def __init__(self, sessionmaker: async_sessionmaker):
        self._sm = sessionmaker

    async def get_owned(self, conversation_id: str, user_id: str) -> Conversation | None:
        async with self._sm() as s:
            c = await s.get(Conversation, conversation_id)
        return c if c and c.user_id == user_id else None

    async def start_turn(
        self, user_id: str, conversation_id: str | None, message: str, images: list[tuple[str, bytes]] | None = None
    ) -> Started:
        """存使用者的訊息（和附圖 (mime, data)）；沒有指定對話就開新對話（標題用第一句話）。"""
        async with self._sm() as s:
            conv = await s.get(Conversation, conversation_id) if conversation_id else None
            if conv is None or conv.user_id != user_id:
                title = " ".join(message.split())[:TITLE_CHARS] or "新對話"
                conv = Conversation(user_id=user_id, title=title)
                s.add(conv)
                await s.flush()
            conv.updated_at = _now()
            rows = [
                ChatImage(user_id=user_id, conversation_id=conv.id, mime=mime, data=data, size=len(data))
                for mime, data in images or []
            ]
            s.add_all(rows)
            await s.flush()
            ids = [r.id for r in rows]
            s.add(Message(conversation_id=conv.id, role="user", content=message, meta={"images": ids} if ids else None))
            await s.commit()
            return Started(conv.id, ids)

    async def get_image(self, image_id: str, user_id: str) -> ChatImage | None:
        """只有上傳的人拿得到（管理員也一樣）。"""
        async with self._sm() as s:
            img = await s.get(ChatImage, image_id)
        return img if img and img.user_id == user_id else None

    async def finish_turn(
        self, conversation_id: str, run_id: str, answer: str, status: str, meta: dict | None = None
    ) -> None:
        async with self._sm() as s:
            s.add(Message(conversation_id=conversation_id, role="assistant", content=answer, run_id=run_id, status=status, meta=meta))
            conv = await s.get(Conversation, conversation_id)
            if conv:
                conv.updated_at = _now()
            await s.commit()

    async def recent_turns(self, conversation_id: str, limit: int) -> list[Turn]:
        """最近 limit 輪「有回答」的問答（舊 → 新），不含目前這一輪剛存進去的提問。"""
        if limit <= 0:
            return []
        async with self._sm() as s:
            rows = list(
                await s.scalars(
                    select(Message).where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.desc()).limit(limit * 2 + 1)
                )
            )
        rows.reverse()
        if rows and rows[-1].role == "user":
            rows = rows[:-1]  # 目前這一輪的提問
        turns: list[Turn] = []
        pending: str | None = None
        for m in rows:
            if m.role == "user":
                pending = m.content
            elif pending is not None:
                if m.status == "done" and m.content.strip():
                    turns.append(Turn(_clip(pending), _clip(m.content)))
                pending = None
        return turns[-limit:]
