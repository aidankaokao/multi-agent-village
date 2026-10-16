"""Phase 3：平行步驟、逾時、取消、審核員、執行紀錄與村規。"""

import asyncio
import json

import httpx
import pytest

from test_phase1 import FakeLLM, Recorder, registry  # noqa: F401
from village.core.executor import Runner
from village.store.settings import VillageRules

PLAN_TWO_JOKES = json.dumps(
    {
        "steps": [
            {"id": "s1", "agent": "poet", "instruction": "寫一首關於海的詩", "use": []},
            {"id": "s2", "agent": "jester", "instruction": "講一個關於海的笑話", "use": []},
        ]
    },
    ensure_ascii=False,
)
PLAN_CHAIN = json.dumps(
    {
        "steps": [
            {"id": "s1", "agent": "poet", "instruction": "寫一首關於貓的詩", "use": []},
            {"id": "s2", "agent": "jester", "instruction": "把這首詩改成笑話", "use": ["s1"]},
        ]
    },
    ensure_ascii=False,
)


def role(kw) -> str:
    system = kw.get("system") or ""
    if "審核員" in system:
        return "reviewer"
    if kw.get("json_schema"):
        return "planner"
    return "poet" if "詩人" in system else "jester" if "笑話師" in system else "mayor"


class Rules:
    """測試用的 RulesStore：直接給 VillageRules（略過 ge=10 秒等限制）。"""

    def __init__(self, **kw):
        self.current = VillageRules.model_construct(**{**VillageRules().model_dump(), **kw})


async def test_parallel_steps_overlap_and_combined_answer(registry):  # noqa: F811
    running, peak = 0, 0

    class SlowLLM(FakeLLM):
        async def chat(self, target, messages, **kw):
            nonlocal running, peak
            if role(kw) in ("poet", "jester"):
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.05)
                running -= 1
            return await super().chat(target, messages, **kw)

    def handler(target, messages, kw):
        return {"planner": PLAN_TWO_JOKES, "poet": "海浪", "jester": "魚說"}[role(kw)]

    runner = Runner(registry, SlowLLM(handler), Recorder(), handoff_delay=0, rules=Rules(reviewer_enabled=False))
    out = await runner.run("r1", "寫一首關於海的詩，也講一個海的笑話")
    assert peak == 2  # 兩步同時在跑
    assert out["ok"] and out["answer"] == "【詩人】寫一首關於海的詩\n海浪\n\n【笑話師】講一個關於海的笑話\n魚說"

    peak = 0
    runner = Runner(registry, SlowLLM(handler), Recorder(), handoff_delay=0, rules=Rules(reviewer_enabled=False, parallel=False))
    await runner.run("r2", "寫一首關於海的詩，也講一個海的笑話")
    assert peak == 1  # 關掉平行就依序


async def test_step_timeout(registry):  # noqa: F811
    class Hang(FakeLLM):
        async def chat(self, target, messages, **kw):
            if role(kw) == "jester":
                await asyncio.sleep(5)
            return await super().chat(target, messages, **kw)

    def handler(target, messages, kw):
        return {"planner": PLAN_CHAIN, "poet": "詩", "jester": "笑話"}[role(kw)]

    bus = Recorder()
    runner = Runner(registry, Hang(handler), bus, handoff_delay=0, rules=Rules(reviewer_enabled=False, step_timeout_s=0.05))
    out = await runner.run("r1", "寫一首關於貓的詩，再改成笑話")
    assert not out["ok"] and "逾時" in out["steps"][1]["error"]
    assert bus.types()[-2:] == ["agent.error", "run.finished"]


async def test_reviewer_rejects_then_passes(registry):  # noqa: F811
    verdicts = iter([
        {"pass": False, "reason": "不是笑話", "fix": "加一個笑點", "step": "s2"},
        {"pass": True, "reason": "有笑點", "fix": "", "step": "s2"},
    ])
    jokes = iter(["一段平淡的話", "貓說：我在充電"])

    def handler(target, messages, kw):
        r = role(kw)
        if r == "reviewer":
            assert "使用者的請求：寫一首關於貓的詩，再改成笑話" in messages[0].content
            return json.dumps(next(verdicts), ensure_ascii=False)
        if r == "jester" and "審核員退回" in messages[0].content:
            assert "加一個笑點" in messages[0].content and "一段平淡的話" in messages[0].content
        return {"planner": PLAN_CHAIN, "poet": "窗台上的貓"}.get(r) or next(jokes)

    bus = Recorder()
    runner = Runner(registry, FakeLLM(handler), bus, handoff_delay=0, rules=Rules())
    out = await runner.run("r1", "寫一首關於貓的詩，再改成笑話")
    assert out["ok"] and out["answer"] == "貓說：我在充電"
    assert [rv["passed"] for rv in out["reviews"]] == [False, True]
    assert out["steps"][1]["rejected"] == [{"output": "一段平淡的話", "reason": "不是笑話"}]
    t = bus.types()
    assert t.count("review.started") == 2 and "agent.retry" in t
    assert t.index("agent.retry") > t.index("review.finished")


async def test_reviewer_gives_up_after_redo_and_skips_when_unavailable(registry):  # noqa: F811
    def handler(target, messages, kw):
        r = role(kw)
        if r == "reviewer":
            return json.dumps({"pass": False, "reason": "不好", "fix": "改", "step": "s2"})
        return {"planner": PLAN_CHAIN, "poet": "詩", "jester": "笑話"}[r]

    out = await Runner(registry, FakeLLM(handler), Recorder(), handoff_delay=0, rules=Rules(review_redo=1)).run("r", "x 詩 笑話")
    assert out["ok"] and len(out["reviews"]) == 2 and out["answer"] == "笑話"  # 重做一次後照實交出

    def broken(target, messages, kw):
        r = role(kw)
        if r == "reviewer":
            return "我覺得還不錯"  # 不是 JSON → 略過審核，不擋答案
        return {"planner": PLAN_CHAIN, "poet": "詩", "jester": "笑話"}[r]

    bus = Recorder()
    out = await Runner(registry, FakeLLM(broken), bus, handoff_delay=0, rules=Rules()).run("r", "x 詩 笑話")
    assert out["ok"] and out["reviews"] == []
    assert next(e for e in bus.events if e["type"] == "review.finished")["skipped"] is True


# ---------------------------------------------------------------- API：紀錄、村規、取消


@pytest.fixture
async def app_env(tmp_path, monkeypatch):
    from test_villagers import FakeClient

    from village.config import REPO_ROOT, Settings
    from village.main import create_app

    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY", "VILLAGE_ADMIN_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/v.db", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    app = create_app(settings, client_factory=FakeClient, handoff_delay=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c, app


async def _wait_done(c, run_id):
    for _ in range(100):
        r = (await c.get(f"/api/runs/{run_id}")).json()
        if r["status"] != "running":
            return r
        await asyncio.sleep(0.02)
    raise AssertionError("run did not finish")


async def test_rules_api_and_trace(app_env):
    c, app = app_env
    rules = (await c.get("/api/rules")).json()
    assert rules["reviewer_enabled"] is True and rules["step_timeout_s"] == 180
    rules.update(reviewer_enabled=False, parallel=False)
    assert (await c.put("/api/rules", json=rules)).json()["parallel"] is False
    assert (await c.put("/api/rules", json={**rules, "step_timeout_s": 1})).status_code == 422
    assert app.state.ctx.rules.current.reviewer_enabled is False

    # 沒有註冊模型：村長退回關鍵字規劃，詩人呼叫 LLM 失敗 → 紀錄為 error，但整個流程有留下來
    run_id = (await c.post("/api/chat", json={"message": "寫一首詩"})).json()["run_id"]
    detail = await _wait_done(c, run_id)
    assert detail["status"] == "error" and detail["agents"] == ["poet"]
    assert detail["plan"]["source"] == "keyword" and detail["steps"][0]["error"]
    lst = (await c.get("/api/runs", params={"q": "寫一首"})).json()
    assert [r["id"] for r in lst] == [run_id]
    assert (await c.get("/api/runs", params={"q": "不存在"})).json() == []
    assert (await c.delete(f"/api/runs/{run_id}")).status_code == 204
    assert (await c.get(f"/api/runs/{run_id}")).status_code == 404


async def test_cancel_and_interrupted(app_env, monkeypatch):
    c, app = app_env
    ctx = app.state.ctx

    async def slow_run(run_id, message, traced=False):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await ctx.trace.finish(run_id, status="cancelled")
            raise

    monkeypatch.setattr(ctx.runner, "run", slow_run)
    run_id = (await c.post("/api/chat", json={"message": "慢慢來"})).json()["run_id"]
    await asyncio.sleep(0.05)
    assert (await c.post(f"/api/chat/{run_id}/cancel")).status_code == 202
    assert (await _wait_done(c, run_id))["status"] == "cancelled"
    assert (await c.post(f"/api/chat/{run_id}/cancel")).status_code == 404

    await ctx.trace.start("stale", "重啟前沒跑完")
    assert await ctx.trace.mark_interrupted() == 1
    assert (await c.get("/api/runs/stale")).json()["status"] == "interrupted"


async def test_runner_cancel_emits_event(registry):  # noqa: F811
    class Hang(FakeLLM):
        async def chat(self, target, messages, **kw):
            if role(kw) == "poet":
                await asyncio.sleep(5)
            return await super().chat(target, messages, **kw)

    bus = Recorder()
    runner = Runner(registry, Hang(lambda t, m, kw: PLAN_CHAIN), bus, handoff_delay=0, rules=Rules(reviewer_enabled=False))
    task = asyncio.create_task(runner.run("r1", "詩 笑話"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bus.types()[-1] == "run.cancelled"
