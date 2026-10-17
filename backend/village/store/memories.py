"""長期記憶的存取：挑出跟這次問答相關的記憶、去除重複。"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from village.store.models import Memory

MAX_CONTENT = 300
ALWAYS_BELOW = 15  # 記憶不多時全部帶入（省掉挑選的誤差）


@dataclass
class MemoryItem:
    id: str
    content: str
    kind: str


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower())


def _bigrams(text: str) -> set[str]:
    t = _norm(text)
    return {t[i : i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


def similarity(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def is_duplicate(content: str, existing: list[str]) -> bool:
    n = _norm(content)
    return any(n == _norm(e) or (n and n in _norm(e)) or similarity(content, e) >= 0.7 for e in existing)


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryStore:
    def __init__(self, sessionmaker: async_sessionmaker):
        self._sm = sessionmaker

    async def all(self, user_id: str, enabled_only: bool = False) -> list[Memory]:
        stmt = select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at)
        if enabled_only:
            stmt = stmt.where(Memory.enabled)
        async with self._sm() as s:
            return list(await s.scalars(stmt))

    async def relevant(self, user_id: str, message: str, limit: int) -> list[MemoryItem]:
        """挑出這次問答要帶入的記憶：不多就全部；多的話偏好類一定帶，其餘依字詞重疊度排序。"""
        rows = await self.all(user_id, enabled_only=True)
        if len(rows) > ALWAYS_BELOW:
            prefs = [m for m in rows if m.kind == "preference"]
            facts = sorted((m for m in rows if m.kind != "preference"), key=lambda m: similarity(message, m.content), reverse=True)
            rows = (prefs[: limit // 2] + facts)[:limit]
        else:
            rows = rows[-limit:]
        if rows:
            async with self._sm() as s:
                await s.execute(
                    update(Memory).where(Memory.id.in_([m.id for m in rows]))
                    .values(use_count=Memory.use_count + 1, last_used_at=_now())
                )
                await s.commit()
        return [MemoryItem(m.id, m.content, m.kind) for m in rows]

    async def add(self, user_id: str, content: str, kind: str, source: str, run_id: str | None = None) -> Memory | None:
        """新增；跟既有記憶重複就不加（回傳 None）。"""
        content = " ".join(content.split())[:MAX_CONTENT]
        if not content:
            return None
        existing = [m.content for m in await self.all(user_id)]
        if is_duplicate(content, existing):
            return None
        m = Memory(user_id=user_id, content=content, kind=kind if kind in ("preference", "fact") else "fact", source=source, source_run_id=run_id)
        async with self._sm() as s:
            s.add(m)
            await s.commit()
        return m

    async def remove(self, user_id: str, ids: list[str]) -> list[str]:
        """刪除自己的記憶，回傳被刪掉的內容。"""
        if not ids:
            return []
        async with self._sm() as s:
            rows = list(await s.scalars(select(Memory).where(Memory.user_id == user_id, Memory.id.in_(ids))))
            for m in rows:
                await s.delete(m)
            await s.commit()
        return [m.content for m in rows]
