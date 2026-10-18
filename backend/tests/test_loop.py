"""迴圈：改到過關為止（審查不通過就退回重做）。"""

import json

import pytest

from village.core import loop as loops
from village.core.executor import Runner
from village.core.orchestrator import Step, make_plan
from village.core.registry import AgentRegistry, load_dir
from village.store.settings import VillageRules
from tests.test_phase1 import AGENTS_DIR, FakeLLM, Recorder


@pytest.fixture
def registry():
    specs, errors = load_dir(AGENTS_DIR)
    assert errors == {}
    return AgentRegistry(specs)


class Rules:
    def __init__(self, **kw):
        self.current = VillageRules(reviewer_enabled=False, parallel=False, **kw)
        self.keep_runs = 100


def role(kw) -> str:
    system = kw.get("system") or ""
    if kw.get("json_schema") and "判讀一段審查意見" in system:
        return "judge"
    if kw.get("json_schema"):
        return "planner"
    for name, r in (("審稿人", "editor"), ("詩人", "poet"), ("笑話師", "jester")):
        if name in system:
            return r
    return "mayor"


LOOP_PLAN = json.dumps({
    "steps": [{"id": "s1", "agent": "poet", "instruction": "寫一首關於海的詩", "use": []},
              {"id": "s2", "agent": "editor", "instruction": "檢查有沒有押韻", "use": ["s1"]}],
    "loop": {"check": "s2", "back_to": "s1", "max_rounds": 3},
})


def test_parse_verdict_and_rounds():
    v = loops.parse_verdict("第二句沒押韻，改成「浪」。\n結論：不通過")
    assert v and not v.passed and "沒押韻" in v.feedback and "結論" not in v.feedback
    assert loops.parse_verdict("寫得很好。\n**結論：通過**").passed
    assert loops.parse_verdict("提到「結論：通過」的標準…\n結論：不通過").passed is False  # 取最後一個
    assert loops.parse_verdict("看起來不錯") is None
    assert loops.rounds_in("不通過就重寫，最多三輪") == 3 and loops.rounds_in("最多 2 次") == 2
    assert loops.rounds_in("寫一首詩") == 0


def test_loop_from_message_and_validate():
    steps = [Step("s1", "taiwan", "查資料"), Step("s2", "poet", "寫成說明", ["s1"]), Step("s3", "editor", "檢查來源", ["s2"])]
    assert loops.from_message(steps, "寫一段說明") is None  # 沒要求迴圈
    lp = loops.from_message(steps, "審稿人檢查，不通過就改，最多 2 輪")
    assert (lp.check, lp.back_to, lp.max_rounds) == ("s3", "s2", 2)
    assert loops.from_message(steps, "沒附來源就補資料再改，直到通過為止").back_to == "s1"
    assert loops.validate(loops.Loop("s3", "s3"), steps) is None  # 退回點要在審查步驟之前
    assert loops.validate(loops.Loop("s1", "s1"), steps) is None
    assert loops.validate(loops.Loop("s3", "s1", 9), steps).max_rounds == loops.MAX_ROUNDS


async def test_planner_loop_and_fallbacks(registry):
    llm = FakeLLM(lambda t, m, kw: LOOP_PLAN)
    plan = await make_plan("寫一首關於海的詩，審稿人檢查押韻，不通過就重寫，最多 3 輪", registry.all(), llm)
    assert plan.loop == loops.Loop("s2", "s1", 3) and plan.to_dict()["loop"]["check"] == "s2"
    assert "loop" in llm.calls[0]["json_schema"]["properties"] and '"loop":{"check"' in llm.calls[0]["system"]

    # LLM 沒排迴圈（弱模型常見）：程式從請求補上
    no_loop = json.loads(LOOP_PLAN) | {"loop": None}
    plan = await make_plan("寫一首海的詩，審稿人檢查押韻，改到通過為止", registry.all(), FakeLLM(lambda t, m, kw: json.dumps(no_loop)))
    assert plan.loop == loops.Loop("s2", "s1", 0) and any("不通過就重做" in n for n in plan.notes)

    # @指名
    plan = await make_plan("@poet 寫一首關於海的詩 → @editor 檢查押韻（不通過就重寫，最多 2 輪）", registry.all(), llm)
    assert plan.source == "mention" and plan.loop == loops.Loop("s2", "s1", 2)


def editor_says(*verdicts):
    """審稿人依序回覆；詩人每次寫出「第 N 版」。"""
    state = {"poem": 0, "edit": 0}

    def handler(t, m, kw):
        r = role(kw)
        if r == "planner":
            return LOOP_PLAN
        if r == "poet":
            state["poem"] += 1
            return f"海浪第{state['poem']}版"
        if r == "editor":
            v = verdicts[min(state["edit"], len(verdicts) - 1)]
            state["edit"] += 1
            return v
        if r == "judge":
            return '{"passed": false, "feedback": "押韻不夠"}'
        return "村長"

    return handler


async def test_loop_retries_until_pass(registry):
    bus = Recorder()
    llm = FakeLLM(editor_says("第二句沒押韻。\n結論：不通過", "很好。\n結論：通過"))
    out = await Runner(registry, llm, bus, handoff_delay=0, rules=Rules()).run("r1", "寫海的詩，審稿人檢查，不通過就重寫")
    assert out["ok"] and out["answer"] == "海浪第2版"  # 交出被審查的最後一版，不是審查意見
    s1, s2 = out["steps"]
    assert s1["round"] == 2 and s1["history"][0]["output"] == "海浪第1版"
    assert s2["verdict"]["passed"] and s2["history"][0]["verdict"]["passed"] is False
    # 第 2 輪的詩人拿到上一版與審查意見；審稿人的指示有補上結論格式
    poet2 = [c for c in llm.calls if role(c) == "poet"][1]["messages"][-1].content
    assert "第二句沒押韻" in poet2 and "你上一版的成果" in poet2 and "海浪第1版" in poet2
    assert "結論：通過" in [c for c in llm.calls if role(c) == "editor"][0]["messages"][-1].content
    verdicts = [e for e in bus.events if e["type"] == "loop.verdict"]
    assert [(v["passed"], v["retry"], v["round"]) for v in verdicts] == [(False, True, 1), (True, False, 2)]
    assert any(e["type"] == "agent.handoff" and e["from_agent"] == "editor" and e["to_agent"] == "poet" for e in bus.events)
    assert [e["round"] for e in bus.events if e["type"] == "agent.started" and e["agent"] == "poet"] == [1, 2]
    assert out["plan"]["loop"] == {"check": "s2", "back_to": "s1", "max_rounds": 3}


async def test_loop_stops_at_max_rounds(registry):
    bus = Recorder()
    llm = FakeLLM(editor_says("還是沒押韻。\n結論：不通過"))
    out = await Runner(registry, llm, bus, handoff_delay=0, rules=Rules(loop_max_rounds=2)).run("r2", "海的詩，不通過就重寫")
    assert out["answer"].startswith("海浪第2版") and "到第 2 輪仍未通過" in out["answer"] and "還是沒押韻" in out["answer"]
    assert len([c for c in llm.calls if role(c) == "poet"]) == 2  # 村規上限 2 輪（計畫寫 3）
    last = [e for e in bus.events if e["type"] == "loop.verdict"][-1]
    assert last["retry"] is False and "2/2" in last["stop"]


async def test_unclear_verdict_uses_llm_then_passes(registry):
    bus = Recorder()

    def handler(t, m, kw):
        r = role(kw)
        if r == "judge":
            raise loops.LLMError("掛了")
        return editor_says("我覺得還行吧")(t, m, kw)

    out = await Runner(registry, FakeLLM(handler), bus, handoff_delay=0, rules=Rules()).run("r3", "海的詩，不通過就重寫")
    s2 = out["steps"][1]
    assert out["answer"] == "海浪第1版" and s2["verdict"]["how"] == "判斷不出來，當作通過"
    assert any("判斷不出來" in w for w in s2["warnings"])

    out = await Runner(registry, FakeLLM(editor_says("押韻不太行")), Recorder(), handoff_delay=0,
                       rules=Rules(loop_max_rounds=2)).run("r4", "海的詩，不通過就重寫")
    assert out["steps"][1]["verdict"]["how"] == "LLM 判斷" and out["steps"][0]["round"] == 2
