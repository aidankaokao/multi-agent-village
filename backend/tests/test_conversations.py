"""Phase 5B：對話紀錄與多輪上下文。"""

import asyncio
import json

import httpx
import pytest

from test_phase1 import FakeLLM, Recorder, registry  # noqa: F401
from test_villagers import FakeClient, _register_model
from village.config import REPO_ROOT, Settings
from village.core.context import PREV, Turn, refers_back
from village.core.executor import Runner
from village.core.orchestrator import PlanError, make_plan, parse_plan, planner_system_prompt
from village.main import create_app

HISTORY = [Turn("寫一首關於海的詩", "海浪輕拍岸邊\n夕陽沉入水面")]


def test_refers_back():
    assert refers_back("再把它翻成英文") and refers_back("把上面那首改成笑話") and refers_back("translate it")
    assert not refers_back("寫一首關於山的詩")


def test_parse_plan_prev_only_with_history(registry):  # noqa: F811
    agents = {a.id: a for a in registry.all()}
    text = json.dumps({"steps": [{"id": "s1", "agent": "jester", "instruction": "把上一輪的詩改成笑話", "use": [PREV]}]})
    assert parse_plan(text, agents, allow_prev=True)[0].use == [PREV]
    with pytest.raises(PlanError):
        parse_plan(text, agents)  # 沒有前一輪就不能用 prev
    assert "最近的對話" in planner_system_prompt(registry.all(), HISTORY)
    assert "最近的對話" not in planner_system_prompt(registry.all())


async def test_fallback_plans_link_previous(registry):  # noqa: F811
    def down(t, m, kw):
        from village.llm.base import LLMError

        raise LLMError("no llm")

    plan = await make_plan("@jester 把它改成笑話", registry.all(), FakeLLM(down), history=HISTORY)
    assert plan.steps[0].use == [PREV] and "上一輪" in plan.notes[-1]
    plan = await make_plan("把剛剛的詩改成笑話", registry.all(), FakeLLM(down), history=HISTORY)
    assert plan.source == "keyword" and plan.steps[0].use == [PREV]
    plan = await make_plan("@jester 講個新笑話", registry.all(), FakeLLM(down), history=HISTORY)
    assert plan.steps[0].use == []  # 沒提到前面的內容就不帶


async def test_runner_passes_previous_answer(registry):  # noqa: F811
    plan = json.dumps({"steps": [{"id": "s1", "agent": "jester", "instruction": "把上一輪的詩改成笑話", "use": [PREV]}]})
    seen = {}

    def handler(target, messages, kw):
        if kw.get("json_schema"):
            assert "最近的對話" in kw["system"] and "海浪輕拍岸邊" in kw["system"]
            return plan
        seen["input"] = messages[0].content
        return "海浪說：我只是來打個招呼"

    out = await Runner(registry, FakeLLM(handler), Recorder(), handoff_delay=0).run("r", "把它改成笑話", history=HISTORY)
    assert out["ok"] and "參考資料（上一輪的回答）" in seen["input"] and "夕陽沉入水面" in seen["input"]


async def test_direct_reply_sees_history(registry):  # noqa: F811
    got = {}

    def handler(target, messages, kw):
        if kw.get("json_schema"):
            return '{"steps":[]}'
        got["messages"] = [(m.role, m.content) for m in messages]
        return "你剛剛請詩人寫了一首海的詩"

    await Runner(registry, FakeLLM(handler), Recorder(), handoff_delay=0).run("r", "我剛剛問了什麼？", history=HISTORY)
    assert got["messages"][:2] == [("user", "寫一首關於海的詩"), ("assistant", "海浪輕拍岸邊\n夕陽沉入水面")]


@pytest.fixture
async def app(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/v.db", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    a = create_app(settings, client_factory=FakeClient, handoff_delay=0)
    async with a.router.lifespan_context(a):
        yield a


async def _wait_answer(c, conv_id, n):
    for _ in range(200):
        msgs = (await c.get(f"/api/conversations/{conv_id}/messages")).json()
        if len(msgs) >= n:
            return msgs
        await asyncio.sleep(0.02)
    raise AssertionError("no answer")


async def test_conversation_api_and_history(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c, \
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as eve:
        await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
        await eve.post("/api/auth/register", json={"username": "eve", "password": "password123"})
        await _register_model(c)
        rules = (await c.get("/api/rules")).json()
        await c.put("/api/rules", json={**rules, "reviewer_enabled": False})

        r = (await c.post("/api/chat", json={"message": "把「貓」翻成英文"})).json()
        conv = r["conversation_id"]
        msgs = await _wait_answer(c, conv, 2)
        assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["run_id"] == r["run_id"]
        assert msgs[1]["status"] in ("done", "error")

        r2 = (await c.post("/api/chat", json={"message": "再翻一次", "conversation_id": conv})).json()
        assert r2["conversation_id"] == conv
        await _wait_answer(c, conv, 4)
        lst = (await c.get("/api/conversations")).json()
        assert lst[0]["id"] == conv and lst[0]["messages"] == 4 and lst[0]["title"].startswith("把「貓」")

        # 別人的對話：看不到、接不上（會開新對話）、刪不掉
        assert (await eve.get("/api/conversations")).json() == []
        assert (await eve.get(f"/api/conversations/{conv}/messages")).status_code == 404
        assert (await eve.delete(f"/api/conversations/{conv}")).status_code == 404

        assert (await c.patch(f"/api/conversations/{conv}", json={"title": "翻譯練習"})).json()["title"] == "翻譯練習"
        assert (await c.delete(f"/api/conversations/{conv}")).status_code == 204
        assert (await c.get("/api/conversations")).json() == []


async def test_recent_turns_skips_failed_and_current(app):
    store = app.state.ctx.conversations
    uid = "u1"
    import sqlite3  # noqa: F401
    async with app.state.ctx.sessionmaker() as s:
        from village.store.models import User

        s.add(User(id=uid, username="u1", display_name="u1", password_hash="x"))
        await s.commit()
    conv = (await store.start_turn(uid, None, "第一題")).conversation_id
    await store.finish_turn(conv, "r1", "第一個回答", "done")
    await store.start_turn(uid, conv, "第二題")
    await store.finish_turn(conv, "r2", "", "error")
    await store.start_turn(uid, conv, "第三題（目前這一輪）")
    turns = await store.recent_turns(conv, 3)
    assert [(t.user, t.answer) for t in turns] == [("第一題", "第一個回答")]
    assert await store.recent_turns(conv, 0) == []
