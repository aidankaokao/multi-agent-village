import json
import os
from pathlib import Path

import httpx
import pytest

from village.config import REPO_ROOT, Settings
from village.core.executor import Runner
from village.core.harness import check_output, mask_profanity, normalize
from village.core.card import Checks
from village.core.orchestrator import PlanError, make_plan, parse_plan, plan_from_mentions
from village.core.registry import AgentRegistry
from village.events import EventBus
from village.llm.base import ChatResult, LLMError
from village.main import create_app

AGENTS_DIR = REPO_ROOT / "agents"


class FakeLLM:
    """handler(target, messages, kw) -> str 或 raise LLMError；記錄每次呼叫。"""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    async def chat(self, target, messages, **kw):
        self.calls.append({"target": target, "messages": messages, **kw})
        return ChatResult(text=self.handler(target, messages, kw), model="fake-model")


def role_of(kw) -> str:
    if kw.get("json_schema"):
        return "planner"
    system = kw.get("system") or ""
    if "詩人" in system:
        return "poet"
    if "笑話師" in system:
        return "jester"
    return "mayor"


@pytest.fixture
def registry():
    r = AgentRegistry(AGENTS_DIR)
    r.refresh()
    return r


class Recorder(EventBus):
    def __init__(self):
        super().__init__()
        self.events: list[dict] = []

    async def publish(self, type_, **data):
        self.events.append({"type": type_, **data})

    def types(self):
        return [e["type"] for e in self.events]


PLAN_POEM_JOKE = json.dumps(
    {
        "steps": [
            {"id": "s1", "agent": "poet", "instruction": "寫一首關於貓的詩", "use": []},
            {"id": "s2", "agent": "jester", "instruction": "把這首詩改成笑話", "use": ["s1"]},
        ]
    },
    ensure_ascii=False,
)


# ---------------------------------------------------------------- registry


def test_registry_loads_repo_agents(registry):
    assert {a.id for a in registry.all()} >= {"poet", "jester"}
    poet = registry.get("poet")
    assert poet.village.sprite == "poet" and poet.prompt and "{instruction}" in poet.prompt.user_template
    assert registry.errors == {}


def test_registry_reports_bad_agents_and_hot_reloads(tmp_path: Path):
    (tmp_path / "good").mkdir()
    good = tmp_path / "good" / "agent.yaml"
    good.write_text("card: {id: good, name: 好人, description: d}\nprompt: {system: s}\n")
    (tmp_path / "wrongdir").mkdir()
    (tmp_path / "wrongdir" / "agent.yaml").write_text("card: {id: other, name: x, description: d}\nprompt: {system: s}\n")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "agent.yaml").write_text("card: [oops")
    (tmp_path / "mayor").mkdir()
    (tmp_path / "mayor" / "agent.yaml").write_text("card: {id: mayor, name: x, description: d}\nprompt: {system: s}\n")

    r = AgentRegistry(tmp_path)
    assert r.refresh() is True
    assert [a.id for a in r.all()] == ["good"]
    assert set(r.errors) == {"wrongdir", "broken", "mayor"}
    assert r.refresh() is False  # 沒變動

    good.write_text("card: {id: good, name: 改名, description: d}\nprompt: {system: s}\n")
    st = good.stat()
    os.utime(good, (st.st_atime, st.st_mtime + 5))
    assert r.refresh() is True and r.get("good").name == "改名"


# ---------------------------------------------------------------- planning


def test_mentions(registry):
    agents = {a.id: a for a in registry.all()}
    p = plan_from_mentions("@poet 寫一首五言絕句 → @jester", agents)
    assert [(s.agent, s.use) for s in p.steps] == [("poet", []), ("jester", ["s1"])]
    assert p.steps[0].instruction == "寫一首五言絕句"
    assert "笑話" in p.steps[1].instruction

    p = plan_from_mentions("@poet 寫一首關於颱風天的詩然後 @jester 改成冷笑話", agents)
    assert [s.instruction for s in p.steps] == ["寫一首關於颱風天的詩", "改成冷笑話"]

    p = plan_from_mentions("請 @poet 寫一首詩，寫到睡著", agents)
    assert p.steps[0].instruction == "請 寫一首詩，寫到睡著"  # 結尾的「著」不能被吃掉

    assert plan_from_mentions("寄信給 @nobody", agents) is None


def test_parse_plan_validation(registry):
    agents = {a.id: a for a in registry.all()}
    steps = parse_plan(f"好的：\n```json\n{PLAN_POEM_JOKE}\n```", agents)
    assert [s.agent for s in steps] == ["poet", "jester"]
    assert parse_plan("<think>嗯</think>{\"steps\": []}", agents) == []
    with pytest.raises(PlanError, match="不存在"):
        parse_plan('{"steps":[{"id":"s1","agent":"wizard","instruction":"x","use":[]}]}', agents)
    with pytest.raises(PlanError, match="use"):
        parse_plan('{"steps":[{"id":"s1","agent":"poet","instruction":"x","use":["s2"]}]}', agents)
    with pytest.raises(PlanError, match="JSON"):
        parse_plan("我覺得應該先寫詩", agents)


async def test_llm_plan_retries_with_feedback(registry):
    answers = iter(["我不會 JSON", PLAN_POEM_JOKE])
    llm = FakeLLM(lambda t, m, kw: next(answers))
    plan = await make_plan("寫一首關於貓的詩，再改成笑話", registry.all(), llm)
    assert plan.source == "llm" and [s.agent for s in plan.steps] == ["poet", "jester"]
    assert "第 1 次規劃無效" in plan.notes[0]
    assert "這個計畫有問題" in llm.calls[1]["messages"][-1].content
    assert llm.calls[0]["target"] == "tier:large" and llm.calls[0]["json_schema"]


async def test_keyword_fallback_when_llm_unavailable(registry):
    def boom(t, m, kw):
        raise LLMError("no model")

    plan = await make_plan("寫一首關於貓的詩，然後把它改成笑話", registry.all(), FakeLLM(boom))
    assert plan.source == "keyword"
    assert [(s.agent, s.use) for s in plan.steps] == [("poet", []), ("jester", ["s1"])]

    plan = await make_plan("今天天氣如何", registry.all(), FakeLLM(boom))
    assert plan.source == "direct" and plan.steps == []


async def test_keyword_fallback_after_repeated_bad_plans(registry):
    plan = await make_plan("講一個笑話", registry.all(), FakeLLM(lambda t, m, kw: "???"))
    assert plan.source == "keyword" and [s.agent for s in plan.steps] == ["jester"]
    assert len(plan.notes) == 4  # 3 次無效 + 改用關鍵字


# ---------------------------------------------------------------- harness


def test_harness():
    assert normalize("<think>想一下</think>\n  貓  ") == "貓"
    assert check_output("", Checks()) and "空" in check_output("", Checks())
    assert "太長" in check_output("x" * 11, Checks(max_chars=10))
    assert "不適當" in check_output("你這個王八蛋", Checks())
    assert check_output("我很能幹，幹嘛這樣", Checks()) is None  # 不誤傷
    assert check_output("what the fuck", Checks()) is not None
    assert mask_profanity("王八蛋 shit") == "＊＊＊ ****"


# ---------------------------------------------------------------- runner


async def test_full_run_poet_to_jester(registry):
    def handler(target, messages, kw):
        role = role_of(kw)
        if role == "planner":
            return PLAN_POEM_JOKE
        if role == "poet":
            return "<think>構思</think>窗台上的貓\n曬著午後的光"
        if role == "jester":
            assert "窗台上的貓" in messages[0].content  # 前一步的成果有傳進來
            assert "參考資料（來自詩人）" in messages[0].content
            return "貓說：我不是在曬太陽，我是在充電。"
        raise AssertionError(role)

    bus = Recorder()
    llm = FakeLLM(handler)
    out = await Runner(registry, llm, bus, handoff_delay=0).run("r1", "寫一首關於貓的詩，再改成笑話")

    assert out["ok"] and out["answer"] == "貓說：我不是在曬太陽，我是在充電。"
    assert out["steps"][0]["output"] == "窗台上的貓\n曬著午後的光"  # <think> 被清掉
    assert bus.types() == [
        "run.started", "plan.created",
        "agent.started", "agent.finished",
        "agent.handoff", "agent.started", "agent.finished",
        "run.finished",
    ]
    handoff = bus.events[4]
    assert (handoff["from_agent"], handoff["to_agent"]) == ("poet", "jester")
    poet_call = next(c for c in llm.calls if role_of(c) == "poet")
    assert poet_call["target"] == "tier:small" and poet_call["temperature"] == 0.9


async def test_step_retry_and_profanity_mask(registry):
    answers = {"poet": iter(["", "一首詩"]), "jester": iter(["媽的好笑", "他媽的還是好笑"])}

    def handler(target, messages, kw):
        role = role_of(kw)
        return PLAN_POEM_JOKE if role == "planner" else next(answers[role])

    bus = Recorder()
    out = await Runner(registry, FakeLLM(handler), bus, handoff_delay=0).run("r2", "詩變笑話")
    poet, jester = out["steps"]
    assert poet["attempts"] == 2 and poet["output"] == "一首詩"
    assert jester["attempts"] == 2 and "媽的" not in jester["output"]
    assert jester["warnings"] == ["已遮蔽不適當用語"]
    assert bus.types().count("agent.retry") == 3


async def test_failed_step_skips_dependents(registry):
    def handler(target, messages, kw):
        role = role_of(kw)
        if role == "planner":
            return PLAN_POEM_JOKE
        raise LLMError("模型掛了")

    bus = Recorder()
    out = await Runner(registry, FakeLLM(handler), bus, handoff_delay=0).run("r3", "詩變笑話")
    assert not out["ok"] and out["answer"] == ""
    assert [s["status"] for s in out["steps"]] == ["error", "error"]
    assert "略過" in out["steps"][1]["error"]
    assert bus.types()[-1] == "run.finished"


async def test_direct_reply(registry):
    def handler(target, messages, kw):
        return '{"steps": []}' if role_of(kw) == "planner" else "你好，我是村長！"

    bus = Recorder()
    out = await Runner(registry, FakeLLM(handler), bus, handoff_delay=0).run("r4", "你好")
    assert out["answer"] == "你好，我是村長！" and out["plan"]["source"] == "direct"


# ---------------------------------------------------------------- API


async def test_agents_and_chat_api(tmp_path, monkeypatch):
    monkeypatch.delenv("VILLAGE_ADMIN_TOKEN", raising=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/v.db", config_dir=tmp_path / "cfg", agents_dir=AGENTS_DIR)
    app = create_app(settings, handoff_delay=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/agents")
            ids = {a["id"]: a for a in r.json()["agents"]}
            assert ids["poet"]["sprite"] == "poet" and ids["poet"]["home"] == [7, 6]

            r = await c.post("/api/chat", json={"message": "講個笑話"})
            assert r.status_code == 202 and r.json()["run_id"]
            assert (await c.post("/api/chat", json={"message": ""})).status_code == 422


def test_prune_unrequested(registry):
    from village.core.orchestrator import Step, prune_unrequested

    agents = {a.id: a for a in registry.all()}
    over = [Step("s1", "poet", "寫一首詩"), Step("s2", "jester", "把這首詩改編成笑話", ["s1"])]
    notes: list[str] = []
    assert [s.agent for s in prune_unrequested(over, "寫一首詩", agents, notes)] == ["poet"]
    assert "移除" in notes[0]

    # 兩者都被要求 → 不動
    assert len(prune_unrequested(over, "寫一首詩再改成笑話", agents, [])) == 2
    # 被依賴的步驟不能移除；只剩一步時也不移除
    rev = [Step("s1", "jester", "講笑話"), Step("s2", "poet", "寫成詩", ["s1"])]
    assert [s.agent for s in prune_unrequested(rev, "講個笑話", agents, [])] == ["jester"]
    assert len(prune_unrequested([Step("s1", "jester", "逗我笑")], "讓我開心一下", agents, [])) == 1
