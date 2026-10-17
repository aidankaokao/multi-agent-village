"""Phase 5C：書記與長期記憶。"""

import asyncio
import json

import httpx
import pytest

from test_phase1 import FakeLLM, Recorder, registry  # noqa: F401
from test_villagers import FakeClient, _register_model
from village.config import REPO_ROOT, Settings
from village.core import scribe
from village.core.executor import Runner
from village.llm.base import LLMError
from village.main import create_app
from village.store.memories import is_duplicate


def test_worth_checking_and_rules():
    assert scribe.worth_checking("我叫 Aidan") and scribe.worth_checking("以後回答簡短一點") and scribe.worth_checking("忘記我的生日")
    assert not scribe.worth_checking("寫一首關於海的詩")
    notes = scribe.by_rules("我叫Aidan，我住在台北，我不喜歡香菜")
    assert ("使用者叫 Aidan", "fact") in notes.add and ("使用者住在台北", "fact") in notes.add
    assert ("使用者不喜歡香菜", "preference") in notes.add and not any("喜歡香菜" in c and "不" not in c for c, _ in notes.add)


async def test_take_notes_add_and_forget_by_number():
    seen = {}

    def handler(target, messages, kw):
        seen["prompt"] = messages[0].content
        return json.dumps({"add": [{"content": "使用者的生日是 6 月 1 日", "kind": "fact"}], "forget": [2, 99]})

    existing = [("m1", "使用者叫 Aidan"), ("m2", "使用者的生日是 5 月 20 日")]
    notes = await scribe.take_notes(FakeLLM(handler), "我的生日其實是 6 月 1 日", existing)
    assert notes.add == [("使用者的生日是 6 月 1 日", "fact")] and notes.forget == ["m2"]  # 99 不存在 → 略過
    assert "2. 使用者的生日是 5 月 20 日" in seen["prompt"]

    skipped = await scribe.take_notes(FakeLLM(handler), "寫一首海的詩", existing)
    assert skipped.source == "skip" and not skipped.add

    def down(t, m, kw):
        raise LLMError("x")

    fb = await scribe.take_notes(FakeLLM(down), "我叫小明", [])
    assert fb.source == "rule" and fb.add == [("使用者叫 小明", "fact")]


def test_duplicate_detection():
    assert is_duplicate("使用者叫 Aidan", ["使用者叫Aidan"]) and is_duplicate("使用者叫 Aidan", ["使用者叫 Aidan，是工程師"])
    assert not is_duplicate("使用者住在台北", ["使用者叫 Aidan"])


async def test_memories_reach_villagers_and_mayor(registry):  # noqa: F811
    seen = {}
    plan = json.dumps({"steps": [{"id": "s1", "agent": "poet", "instruction": "寫一首詩", "use": []}]})

    def handler(target, messages, kw):
        if kw.get("json_schema"):
            return plan
        seen["input"] = messages[0].content
        return "一首詩"

    await Runner(registry, FakeLLM(handler), Recorder(), handoff_delay=0).run("r", "寫一首詩", memories=["使用者喜歡貓"])
    assert "書記記下的長期記憶" in seen["input"] and "- 使用者喜歡貓" in seen["input"]

    def direct(target, messages, kw):
        if kw.get("json_schema"):
            return '{"steps":[]}'
        seen["system"] = kw["system"]
        return "你好 Aidan"

    await Runner(registry, FakeLLM(direct), Recorder(), handoff_delay=0).run("r2", "你好", memories=["使用者叫 Aidan"])
    assert "使用者叫 Aidan" in seen["system"]


@pytest.fixture
async def app(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/v.db", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    a = create_app(settings, client_factory=FakeClient, handoff_delay=0)
    async with a.router.lifespan_context(a):
        yield a


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_memory_api_isolation_and_relevance(app):
    async with client(app) as c, client(app) as eve:
        await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
        await eve.post("/api/auth/register", json={"username": "eve", "password": "password123"})
        m = (await c.post("/api/memories", json={"content": "使用者叫 Aidan", "kind": "fact"})).json()
        assert m["source"] == "manual"
        assert (await c.post("/api/memories", json={"content": "使用者叫Aidan"})).status_code == 409  # 重複
        assert (await eve.get("/api/memories")).json() == []
        assert (await eve.patch(f"/api/memories/{m['id']}", json={"enabled": False})).status_code == 404
        assert (await eve.delete(f"/api/memories/{m['id']}")).status_code == 404
        assert (await c.patch(f"/api/memories/{m['id']}", json={"content": "使用者叫 Aidan Kao"})).json()["content"] == "使用者叫 Aidan Kao"
        await c.patch(f"/api/memories/{m['id']}", json={"enabled": False})

        store = app.state.ctx.memories
        uid = (await c.get("/api/auth/status")).json()["user"]["id"]
        assert await store.relevant(uid, "隨便", 10) == []  # 停用的不帶
        for i in range(20):
            await store.add(uid, f"事實第{i}號：喜歡第{i}種水果", "fact", "manual")
        await store.add(uid, "使用者希望回答簡短", "preference", "manual")
        picked = [x.content for x in await store.relevant(uid, "我喜歡第7種水果嗎", 4)]
        assert "使用者希望回答簡短" in picked and any("第7種" in p for p in picked) and len(picked) == 4
        rows = {x.content: x for x in await store.all(uid)}
        assert rows["使用者希望回答簡短"].use_count == 1

        assert (await c.delete("/api/memories")).status_code == 204
        assert (await c.get("/api/memories")).json() == []


async def test_chat_then_scribe_remembers(app):
    async with client(app) as c:
        await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
        await _register_model(c)  # FakeClient 的回答不是 JSON → 書記改用固定句型
        rules = (await c.get("/api/rules")).json()
        await c.put("/api/rules", json={**rules, "reviewer_enabled": False})
        await c.post("/api/chat", json={"message": "我叫 Aidan，幫我把「貓」翻成英文"})
        for _ in range(200):
            mems = (await c.get("/api/memories")).json()
            if mems:
                break
            await asyncio.sleep(0.02)
        assert [m["content"] for m in mems] == ["使用者叫 Aidan"] and mems[0]["source"] == "auto"

        # 關掉自動記錄就不會記
        await c.delete("/api/memories")
        await c.put("/api/rules", json={**rules, "reviewer_enabled": False, "memory_auto": False})
        await c.post("/api/chat", json={"message": "我叫 Bob"})
        await asyncio.sleep(0.3)
        assert (await c.get("/api/memories")).json() == []
