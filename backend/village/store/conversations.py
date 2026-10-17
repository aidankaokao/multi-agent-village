"""對話紀錄：每個帳號的對話與訊息。問答前讀最近幾輪當上下文，問答結束後把回答存回來。

附圖存在 chat_images（跟著對話保存），使用者訊息的 meta.images 記圖片 id。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from village.core.context import Turn
from village.store.models import ChatImage, Conversation, Message

GENERATED_DAYS = 14  # 畫家畫的圖保存兩週：很少回頭查，「上週那張」還找得到
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

    async def recent_images(self, conversation_id: str, turns: int) -> list[tuple[str, bytes]]:
        """前幾輪（不含目前這一輪）裡最近一次附的圖，給鑑定師追問時重看。超過 turns 輪就不算。"""
        if turns <= 0:
            return []
        async with self._sm() as s:
            rows = list(
                await s.scalars(
                    select(Message).where(Message.conversation_id == conversation_id, Message.role == "user")
                    .order_by(Message.created_at.desc()).limit(turns + 1)
                )
            )
            for m in rows[1:]:  # rows[0] 是目前這一輪的提問
                ids = (m.meta or {}).get("images") or []
                if ids:
                    imgs = {i.id: i for i in await s.scalars(
                        select(ChatImage).where(ChatImage.id.in_(ids), ChatImage.conversation_id == conversation_id)
                    )}
                    return [(imgs[i].mime, imgs[i].data) for i in ids if i in imgs]
        return []

    async def save_generated(
        self, user_id: str, conversation_id: str | None, images: list[tuple[str, bytes]]
    ) -> list[str]:
        """存畫家畫的圖（GENERATED_DAYS 天後自動清掉），回傳圖片 id。"""
        expires = _now() + timedelta(days=GENERATED_DAYS)
        rows = [
            ChatImage(
                user_id=user_id, conversation_id=conversation_id, mime=mime, data=data, size=len(data),
                kind="generated", expires_at=expires,
            )
            for mime, data in images
        ]
        async with self._sm() as s:
            s.add_all(rows)
            await s.commit()
            return [r.id for r in rows]

    async def count_generated_since(self, user_id: str, since: datetime) -> int:
        async with self._sm() as s:
            return await s.scalar(
                select(func.count()).select_from(ChatImage)
                .where(ChatImage.user_id == user_id, ChatImage.kind == "generated", ChatImage.created_at >= since)
            ) or 0

    async def purge_expired(self) -> int:
        async with self._sm() as s:
            n = (await s.execute(delete(ChatImage).where(ChatImage.expires_at < _now()))).rowcount
            await s.commit()
        return n or 0

    async def get_image(self, image_id: str, user_id: str) -> ChatImage | None:
        """只有本人拿得到（管理員也一樣）；過期的畫作即使還沒被清掉也當作不存在。"""
        async with self._sm() as s:
            img = await s.get(ChatImage, image_id)
        if img is None or img.user_id != user_id:
            return None
        if img.expires_at is not None:
            exp = img.expires_at if img.expires_at.tzinfo else img.expires_at.replace(tzinfo=UTC)  # SQLite 讀回來沒有時區
            if exp < _now():
                return None
        return img

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
