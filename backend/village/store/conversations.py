"""對話紀錄：每個帳號的對話與訊息。問答前讀最近幾輪當上下文，問答結束後把回答存回來。

附圖存在 chat_images（跟著對話保存），使用者訊息的 meta.images 記圖片 id。
附的文件存在 chat_files（跟著對話保存），meta.files 記 [{id, name, size}]；駐村專家回傳的報告也存這裡（14 天過期）。
錄音（kind=audio）先上傳、問答時才接到對話（meta.files 標 audio: true）；1 天後過期，聽打員轉完就刪掉。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from village.core.context import Turn
from village.core.docfiles import DocFile
from village.store.models import ChatFile, ChatImage, Conversation, Message

GENERATED_DAYS = 14  # 畫家畫的圖保存兩週：很少回頭查，「上週那張」還找得到
REPORT_DAYS = 14  # 駐村專家回傳的報告（PDF）保存期限同畫作
AUDIO_DAYS = 1  # 上傳的錄音：沒用到（或轉寫失敗）的 1 天後清掉
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
    files: list[dict] = field(default_factory=list)  # [{id, name, size}]


class ConversationStore:
    def __init__(self, sessionmaker: async_sessionmaker):
        self._sm = sessionmaker

    async def get_owned(self, conversation_id: str, user_id: str) -> Conversation | None:
        async with self._sm() as s:
            c = await s.get(Conversation, conversation_id)
        return c if c and c.user_id == user_id else None

    async def start_turn(
        self, user_id: str, conversation_id: str | None, message: str, images: list[tuple[str, bytes]] | None = None,
        files: list[DocFile] | None = None, audio: list[str] | None = None,
    ) -> Started:
        """存使用者的訊息（和附圖 (mime, data)、附的文件）；沒有指定對話就開新對話（標題用第一句話）。"""
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
            docs = [
                ChatFile(user_id=user_id, conversation_id=conv.id, name=f.name, mime=f.mime, data=f.data, size=len(f.data))
                for f in files or []
            ]
            s.add_all(rows + docs)
            await s.flush()
            ids = [r.id for r in rows]
            listed = [{"id": d.id, "name": d.name, "size": d.size} for d in docs]
            if audio:  # 先上傳好的錄音：接到這段對話
                for f in await s.scalars(select(ChatFile).where(ChatFile.id.in_(audio), ChatFile.user_id == user_id)):
                    f.conversation_id = conv.id
                    listed.append({"id": f.id, "name": f.name, "size": f.size, "audio": True})
            meta = {k: v for k, v in (("images", ids), ("files", listed)) if v}
            s.add(Message(conversation_id=conv.id, role="user", content=message, meta=meta or None))
            await s.commit()
            return Started(conv.id, ids, listed)

    async def recent_files(self, conversation_id: str, turns: int) -> list[DocFile]:
        """前幾輪（不含目前這一輪）裡最近一次附的文件：追問（「第二段寫詳細一點」）時再交給駐村專家。"""
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
                ids = [f["id"] for f in (m.meta or {}).get("files") or [] if not f.get("audio")]
                if ids:
                    found = {f.id: f for f in await s.scalars(
                        select(ChatFile).where(
                            ChatFile.id.in_(ids), ChatFile.conversation_id == conversation_id, ChatFile.kind != "audio"
                        )
                    )}
                    return [DocFile(found[i].name, found[i].mime, found[i].data) for i in ids if i in found]
        return []

    async def last_agents(self, conversation_id: str) -> list[str]:
        """上一輪回答是哪些村民做的（追問時判斷要不要再交給同一位）。"""
        async with self._sm() as s:
            msg = await s.scalar(
                select(Message).where(Message.conversation_id == conversation_id, Message.role == "assistant")
                .order_by(Message.created_at.desc()).limit(1)
            )
        return list(((msg.meta or {}).get("agents") or []) if msg else [])

    async def save_report(self, user_id: str, conversation_id: str | None, name: str, mime: str, data: bytes) -> str:
        """存駐村專家回傳的檔案（REPORT_DAYS 天後自動清掉），回傳檔案 id。"""
        row = ChatFile(
            user_id=user_id, conversation_id=conversation_id, kind="report", name=name, mime=mime, data=data,
            size=len(data), expires_at=_now() + timedelta(days=REPORT_DAYS),
        )
        async with self._sm() as s:
            s.add(row)
            await s.commit()
            return row.id

    async def save_audio(self, user_id: str, name: str, mime: str, data: bytes) -> str:
        """存上傳的錄音（還沒接到對話；AUDIO_DAYS 天後自動清掉），回傳檔案 id。"""
        row = ChatFile(
            user_id=user_id, conversation_id=None, kind="audio", name=name, mime=mime, data=data, size=len(data),
            expires_at=_now() + timedelta(days=AUDIO_DAYS),
        )
        async with self._sm() as s:
            s.add(row)
            await s.commit()
            return row.id

    async def pending_audio(self, user_id: str, ids: list[str]) -> list[ChatFile]:
        """這次問答要用的錄音：本人上傳、還沒接到別的對話、還沒過期的（依 ids 的順序）。"""
        if not ids:
            return []
        async with self._sm() as s:
            rows = {f.id: f for f in await s.scalars(
                select(ChatFile).where(
                    ChatFile.id.in_(ids), ChatFile.user_id == user_id, ChatFile.kind == "audio",
                    ChatFile.conversation_id.is_(None), ChatFile.expires_at > _now(),
                )
            )}
        return [rows[i] for i in dict.fromkeys(ids) if i in rows]

    async def delete_file(self, file_id: str) -> None:
        async with self._sm() as s:
            await s.execute(delete(ChatFile).where(ChatFile.id == file_id))
            await s.commit()

    async def get_file(self, file_id: str, user_id: str) -> ChatFile | None:
        """只有本人拿得到（管理員也一樣）；過期的報告即使還沒被清掉也當作不存在。"""
        async with self._sm() as s:
            f = await s.get(ChatFile, file_id)
        if f is None or f.user_id != user_id:
            return None
        if f.expires_at is not None:
            exp = f.expires_at if f.expires_at.tzinfo else f.expires_at.replace(tzinfo=UTC)
            if exp < _now():
                return None
        return f

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
        """清掉過期的畫作與報告。"""
        async with self._sm() as s:
            n = (await s.execute(delete(ChatImage).where(ChatImage.expires_at < _now()))).rowcount or 0
            n += (await s.execute(delete(ChatFile).where(ChatFile.expires_at < _now()))).rowcount or 0
            await s.commit()
        return n

    async def last_painting(self, conversation_id: str, user_id: str) -> tuple[str, bytes] | None:
        """上一輪回答裡的畫作（修圖追問「再改亮一點」用它當底圖）；過期或沒有就回 None。"""
        async with self._sm() as s:
            msg = await s.scalar(
                select(Message).where(Message.conversation_id == conversation_id, Message.role == "assistant")
                .order_by(Message.created_at.desc()).limit(1)
            )
        urls = [a.get("url") or "" for a in ((msg.meta or {}).get("attachments") or []) if a.get("kind") == "image"] if msg else []
        for url in reversed(urls):
            img = await self.get_image(url.rsplit("/", 1)[-1], user_id)
            if img is not None:
                return img.mime, img.data
        return None

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
