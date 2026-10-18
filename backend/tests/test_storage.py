"""儲存空間：過期檔案與太舊的附件會被清掉、用量統計、清掉很多之後壓縮資料庫檔案。"""

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from village.config import Settings
from village.main import create_app
from village.store import storage
from village.store.models import ChatFile, ChatImage


@pytest.fixture
async def env(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    db = tmp_path / "v.db"
    app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{db}", config_dir=tmp_path / "cfg"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
            yield c, app, db


async def test_purge_usage_and_vacuum(env):
    c, app, db = env
    ctx = app.state.ctx
    me = (await c.get("/api/auth/status")).json()["user"]["id"]
    store = ctx.conversations
    started = await store.start_turn(me, None, "附件", images=[("image/png", b"\x89PNG" + b"0" * 1000)])
    big = os.urandom(3 * 1024 * 1024)
    report = await store.save_report(me, started.conversation_id, "r.pdf", "application/pdf", big)
    audio = await store.save_audio(me, "a.mp3", "audio/mpeg", b"ID3" + b"0" * 100)

    u = (await c.get("/api/rules/storage")).json()
    by = {x["label"]: x for x in u["items"]}
    assert by["報告與逐字稿"]["bytes"] == len(big) and by["使用者附的圖"]["count"] == 1 and by["錄音（等待轉寫）"]["count"] == 1
    assert u["db_bytes"] > len(big)

    # 報告到期、附件超過 90 天（村規預設）：都會被清掉；錄音還沒到期留著
    old = datetime.now(UTC) - timedelta(days=91)
    async with ctx.sessionmaker() as s:
        await s.execute(update(ChatFile).where(ChatFile.id == report).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.execute(update(ChatImage).values(created_at=old))
        await s.commit()
    assert ctx.rules.current.attachment_keep_days == 90
    r = (await c.post("/api/rules/storage/cleanup")).json()
    assert r["removed"] == 2 and r["vacuumed"] is True
    assert [x["label"] for x in r["items"]] == ["錄音（等待轉寫）"]
    assert r["db_bytes"] < len(big)  # 壓縮後檔案變小
    async with ctx.sessionmaker() as s:
        assert [f.id for f in await s.scalars(select(ChatFile))] == [audio]


async def test_keep_forever_and_vacuum_threshold(env):
    c, app, _ = env
    ctx = app.state.ctx
    me = (await c.get("/api/auth/status")).json()["user"]["id"]
    await ctx.conversations.start_turn(me, None, "附件", images=[("image/png", b"\x89PNG")])
    async with ctx.sessionmaker() as s:
        await s.execute(update(ChatImage).values(created_at=datetime.now(UTC) - timedelta(days=4000)))
        await s.commit()
    assert await ctx.conversations.purge_expired(0) == 0  # 0 = 永久保存
    assert await storage.vacuum_if_needed(ctx.sessionmaker) is False  # 空出來的空間太少，不壓縮
    rules = (await c.get("/api/rules")).json()
    assert (await c.put("/api/rules", json={**rules, "attachment_keep_days": 30})).json()["attachment_keep_days"] == 30
    assert await ctx.conversations.purge_expired(ctx.rules.current.attachment_keep_days) == 1
