"""儲存空間：定期清理過期的檔案、統計用量、清理後壓縮資料庫檔案。

會累積的東西（都在 DB 裡）：
- chat_images：upload（使用者附的圖）、generated（畫作，14 天）
- chat_files：upload（使用者附的文件）、report（駐村專家的報告、聽打員的逐字稿，14 天）、audio（錄音，轉完就刪、最多 1 天）
有期限的到期就刪；使用者附的圖與文件依村規 `attachment_keep_days` 保存（0 = 跟著對話永久保存）。
中轉站（core/depot.py）本身只在一次問答的記憶體裡，不存任何東西。

SQLite 刪掉資料後檔案不會自己變小（空出來的頁之後會重複使用）；清掉的空間夠多時跑 VACUUM 把檔案縮回來。
"""

import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from village.store.models import ChatFile, ChatImage

log = logging.getLogger("village.storage")

MB = 1024 * 1024
VACUUM_MIN_FREE = 32 * MB  # 空出來的空間超過這麼多、而且超過檔案的 1/4 才壓縮（VACUUM 會重寫整個檔案）

LABELS = {
    ("chat_images", "upload"): "使用者附的圖",
    ("chat_images", "generated"): "畫家的畫作",
    ("chat_images", "report"): "駐村專家的圖（報紙預覽）",
    ("chat_files", "upload"): "使用者附的文件",
    ("chat_files", "report"): "報告與逐字稿",
    ("chat_files", "audio"): "錄音（等待轉寫）",
}


def _now() -> datetime:
    return datetime.now(UTC)


async def purge(sm: async_sessionmaker, attachment_days: int = 0) -> int:
    """清掉過期的畫作、報告、錄音；attachment_days > 0 時也清掉超過天數的使用者附件。回傳刪了幾筆。"""
    now = _now()
    async with sm() as s:
        n = (await s.execute(delete(ChatImage).where(ChatImage.expires_at < now))).rowcount or 0
        n += (await s.execute(delete(ChatFile).where(ChatFile.expires_at < now))).rowcount or 0
        if attachment_days > 0:
            old = now - timedelta(days=attachment_days)
            n += (await s.execute(delete(ChatImage).where(ChatImage.kind == "upload", ChatImage.created_at < old))).rowcount or 0
            n += (await s.execute(delete(ChatFile).where(ChatFile.kind == "upload", ChatFile.created_at < old))).rowcount or 0
        await s.commit()
    return n


def _sqlite_path(sm: async_sessionmaker) -> str | None:
    engine = sm.kw.get("bind")
    if engine is None or engine.dialect.name != "sqlite":
        return None
    db = engine.url.database
    return db if db and db != ":memory:" else None


async def _free_bytes(sm: async_sessionmaker) -> tuple[int, int]:
    """(資料庫檔案大小, 空著可重複使用的空間)；不是 SQLite 回 (0, 0)。"""
    path = _sqlite_path(sm)
    if path is None or not os.path.exists(path):
        return 0, 0
    async with sm() as s:
        conn = await s.connection()
        page = (await conn.exec_driver_sql("PRAGMA page_size")).scalar() or 0
        free = (await conn.exec_driver_sql("PRAGMA freelist_count")).scalar() or 0
    return os.path.getsize(path), page * free


async def vacuum_if_needed(sm: async_sessionmaker, force: bool = False) -> bool:
    """空出來的空間夠多時壓縮 SQLite 檔案；回傳有沒有壓縮。"""
    size, free = await _free_bytes(sm)
    if not size or (not force and (free < VACUUM_MIN_FREE or free < size / 4)) or free == 0:
        return False
    engine = sm.kw["bind"]
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.exec_driver_sql("VACUUM")  # 不能在交易裡跑
    log.info("壓縮資料庫：%.1f MB → %.1f MB", size / MB, (await _free_bytes(sm))[0] / MB)
    return True


async def usage(sm: async_sessionmaker) -> dict:
    """各類檔案的筆數與大小、資料庫檔案大小（給村規頁顯示）。"""
    items = []
    async with sm() as s:
        for model, table in ((ChatImage, "chat_images"), (ChatFile, "chat_files")):
            rows = await s.execute(select(model.kind, func.count(), func.coalesce(func.sum(model.size), 0)).group_by(model.kind))
            for kind, count, size in rows:
                items.append({"key": f"{table}:{kind}", "label": LABELS.get((table, kind), f"{table} {kind}"),
                              "count": int(count), "bytes": int(size)})
    size, free = await _free_bytes(sm)
    order = list(LABELS.values())
    items.sort(key=lambda x: order.index(x["label"]) if x["label"] in order else 99)
    return {"items": items, "db_bytes": size or None, "free_bytes": free or None}
