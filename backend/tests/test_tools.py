"""Phase 4：工具箱（計算機、圖表、日期）與工具型村民。"""

import json

import pytest

from test_phase1 import FakeLLM, Recorder, registry  # noqa: F401
from village.core.card import AgentSpec
from village.core.executor import Runner, StepResult, run_agent
from village.llm.base import LLMError
from village.tools import TOOLS, ToolError
from village.tools.calculator import evaluate, extract_expression, fmt
from village.tools.chart import extract_table
from village.tools.clock import parse_date


@pytest.mark.parametrize(
    "expr,want",
    [
        ("35*12*0.9", "378"), ("2^20", "1,048,576"), ("（3+5）×2", "16"), ("100 * 15%", "15"), ("15 % 4", "3"),
        ("1,234+1", "1,235"), ("sqrt(16)+factorial(5)", "124"), ("10/4", "2.5"), ("0.1+0.2", "0.3"),
    ],
)
def test_calculator_values(expr, want):
    assert fmt(evaluate(expr)) == want


@pytest.mark.parametrize(
    "expr", ["__import__('os').system('ls')", "().__class__", "9**9**9", "10/0", "x+1", "open('a')", "a" * 400, "sqrt(-1)"]
)
def test_calculator_rejects_unsafe_or_invalid(expr):
    with pytest.raises(ToolError):
        evaluate(expr)


def test_fallback_extractors():
    assert extract_expression("123 乘以 456 是多少") == "123 * 456"
    assert extract_expression("2 的 20 次方是多少") == "2 **20"
    assert extract_expression("今天天氣很好") is None
    assert extract_table("一月 120\n二月：98\n- 三月, 1,500") == (["一月", "二月", "三月"], [120.0, 98.0, 1500.0])
    clock = TOOLS["clock"]
    assert clock.fallback("距離 2026-12-25 還有幾天", "")["date2"] == "2026-12-25"
    assert clock.fallback("30 天前是幾號", "")["days"] == -30
    with pytest.raises(ToolError):
        parse_date("明年", "date1")


def test_chart_validation_and_escaping():
    chart = TOOLS["chart"]
    ok = chart.run({
        "chart": "bar", "title": "<script>alert(1)</script>", "x_label": "", "y_label": "",
        "labels": ["A", "B"], "series": [{"name": "n", "values": [1, 2]}],
    })
    svg = ok.attachments[0].data
    assert svg.startswith("<svg") and "<script>" not in svg and "&lt;script&gt;" in svg
    assert "長條圖" in ok.text and "- A：1" in ok.text
    bad = [
        {"labels": [], "series": [{"name": "n", "values": []}]},
        {"labels": ["A", "B"], "series": [{"name": "n", "values": [1]}]},
        {"labels": ["A"], "series": [{"name": "n", "values": ["很多"]}]},
        {"chart": "pie", "labels": ["A", "B"], "series": [{"name": "n", "values": [1, 2]}, {"name": "m", "values": [1, 2]}]},
    ]
    for args in bad:
        with pytest.raises(ToolError):
            chart.run({"chart": "bar", "title": "", "x_label": "", "y_label": "", **args})


CALC = {
    "card": {"id": "calc", "name": "計算機", "description": "精準計算"},
    "kind": "tool",
    "tool": {"id": "calculator", "explain": False},
    "model": {"temperature": 0},
}


def spec(**kw) -> AgentSpec:
    data = json.loads(json.dumps(CALC))
    data["tool"].update(kw)
    return AgentSpec.model_validate(data)


def test_tool_spec_validation():
    assert spec().prompt.system == "" and spec().to_doc()["prompt"]["user_template"]  # 沒寫 prompt 會補上空的
    with pytest.raises(ValueError, match="沒有「magic」這個工具"):
        AgentSpec.model_validate({**CALC, "tool": {"id": "magic"}})
    with pytest.raises(ValueError, match="需要選一個工具"):
        AgentSpec.model_validate({**CALC, "tool": None})
    with pytest.raises(ValueError, match="prompt.system"):
        AgentSpec.model_validate({**CALC, "kind": "prompt", "tool": None, "prompt": {"system": "  "}})


async def test_tool_agent_retries_bad_args_then_succeeds():
    replies = iter(['{"expression": "65*7*打九折"}', '{"expression": "65*7*0.9"}'])

    def handler(target, messages, kw):
        assert kw["json_schema"] == TOOLS["calculator"].schema()
        if len(messages) > 1:
            assert "不認得" in messages[-1].content or "格式錯誤" in messages[-1].content
        return next(replies)

    r = StepResult(id="s1", agent="calc", instruction="", use=[])
    await run_agent(FakeLLM(handler), spec(), "一杯 65 元買 7 杯打 9 折", "", r)
    assert r.output == "65*7*0.9 = 409.5" and r.tool_args == {"expression": "65*7*0.9"}
    assert r.attempts == 2 and len(r.retry_reasons) == 1


async def test_tool_agent_falls_back_without_llm():
    def down(target, messages, kw):
        raise LLMError("no model")

    r = StepResult(id="s1", agent="calc", instruction="", use=[])
    await run_agent(FakeLLM(down), spec(), "123 乘以 456 是多少", "", r)
    assert r.output == "123 * 456 = 56,088" and "改由程式" in r.warnings[0]

    r = StepResult(id="s1", agent="calc", instruction="", use=[])
    with pytest.raises(LLMError, match="計算機沒辦法完成"):
        await run_agent(FakeLLM(down), spec(), "今天天氣如何", "", r)


async def test_tool_agent_explain_must_keep_numbers():
    def handler(target, messages, kw):
        if kw.get("json_schema"):
            return '{"expression": "123*456"}'
        return "答案是 56088。"  # 千分位不同也算一致

    r = StepResult(id="s1", agent="calc", instruction="", use=[])
    await run_agent(FakeLLM(handler), spec(explain=True), "123 乘以 456", "", r)
    assert r.output == "答案是 56088。\n\n123*456 = 56,088"

    def liar(target, messages, kw):
        return '{"expression": "123*456"}' if kw.get("json_schema") else "大約是五萬六千。"

    r = StepResult(id="s1", agent="calc", instruction="", use=[])
    await run_agent(FakeLLM(liar), spec(explain=True), "123 乘以 456", "", r)
    assert r.output == "123*456 = 56,088" and "不一致" in r.warnings[0]


async def test_chart_step_attachments_flow_to_run_finished(registry):  # noqa: F811
    from village.core.registry import AgentRegistry

    chart_spec = AgentSpec.model_validate({
        "card": {"id": "chart", "name": "圖表師", "description": "畫圖"}, "kind": "tool", "tool": {"id": "chart"},
    })
    reg = AgentRegistry([*registry.all(), chart_spec])
    plan = json.dumps({"steps": [{"id": "s1", "agent": "chart", "instruction": "畫圓餅圖", "use": []}]})

    def handler(target, messages, kw):
        if "steps" in json.dumps(kw.get("json_schema") or {}):
            return plan
        return json.dumps({
            "chart": "pie", "title": "支出", "x_label": "", "y_label": "",
            "labels": ["房租", "餐費"], "series": [{"name": "元", "values": [15000, 8000]}],
        })

    bus = Recorder()
    out = await Runner(reg, FakeLLM(handler), bus, handoff_delay=0).run("r1", "畫一個圓餅圖：房租 15000、餐費 8000")
    assert out["ok"] and out["attachments"][0]["kind"] == "svg" and "圓餅圖" in out["answer"]
    finished = next(e for e in bus.events if e["type"] == "agent.finished")
    assert finished["attachments"][0]["title"] == "支出"


async def test_tools_api_and_try(tmp_path, monkeypatch):
    import httpx

    from test_villagers import FakeClient
    from village.config import REPO_ROOT, Settings
    from village.main import create_app

    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY", "VILLAGE_ADMIN_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/v.db", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    app = create_app(settings, client_factory=FakeClient, handoff_delay=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
            tools = (await c.get("/api/villagers/tools")).json()
            assert {t["id"] for t in tools} == {"calculator", "chart", "clock"} and tools[0]["default_prompt"]
            ids = {v["id"] for v in (await c.get("/api/villagers")).json()}
            assert {"calculator", "chart"} <= ids  # 內建範例匯入
            # 沒有註冊模型 → LLM 失敗 → 退路照樣算得出來
            calc = next(v for v in (await c.get("/api/villagers")).json() if v["id"] == "calculator")
            r = (await c.post("/api/villagers/try", json={"spec": calc["spec"], "instruction": "123 乘以 456"})).json()
            assert r["ok"] and "56,088" in r["output"] and r["tool_args"] == {"expression": "123 * 456"}


def test_clock_years_are_decided_by_program(monkeypatch):
    from datetime import date

    import village.tools.clock as clock_mod

    monkeypatch.setattr(clock_mod, "today", lambda: date(2026, 10, 17))
    run = TOOLS["clock"].run

    def ask(op, d1="", d2="", days=0):
        return run({"operation": op, "date1": d1, "date2": d2, "days": days}).text

    # 「距離跨年還有幾天」：抓今年底，不是今年初
    assert ask("until", d2="跨年") == "距離 2026 年 12 月 31 日（星期四）（跨年）還有 75 天"
    # 生日 5/20 今年已經過了 → 明年
    assert ask("until", d2="05-20") == "距離 2027 年 5 月 20 日（星期四）還有 215 天"
    assert ask("until", d2="5月20日").endswith("還有 215 天")
    assert ask("until", d2="10-17").startswith("就是今天")
    assert ask("until", d2="2026-01-01").endswith("已經過了 289 天")  # 使用者明確寫了年份才算過去
    assert ask("between", "03-01", "03-15") == "2026 年 3 月 1 日（星期日） 到 2026 年 3 月 15 日（星期日） 相差 14 天"
    assert ask("between", "12-20", "01-05").endswith("相差 16 天")  # 跨年的區間
    assert ask("add", days=100).endswith("2027 年 1 月 25 日（星期一）")
    assert "今天是 2026-10-17" in TOOLS["clock"].context()
    with pytest.raises(ToolError, match="農曆"):
        ask("until", d2="除夕夜")
    with pytest.raises(ToolError, match="不是存在的日期"):
        ask("until", d2="02-30")

    fb = TOOLS["clock"].fallback
    assert fb("距離跨年還有幾天", "")["date2"] == "跨年" and fb("距離跨年還有幾天", "")["operation"] == "until"
    assert fb("我的生日是 5 月 20 日，還剩幾天", "")["date2"] == "5月20日"
    assert fb("從 3 月 1 日到 3 月 15 日相差幾天？", "")["operation"] == "between"


async def test_tool_prompt_includes_today(monkeypatch):
    seen = {}

    def handler(target, messages, kw):
        seen["system"] = kw["system"]
        return '{"operation":"until","date1":"","date2":"跨年","days":0}'

    s = AgentSpec.model_validate({**CALC, "tool": {"id": "clock"}})
    r = StepResult(id="s1", agent="clock", instruction="", use=[])
    await run_agent(FakeLLM(handler), s, "距離跨年還有幾天", "", r)
    assert "今天是" in seen["system"] and "until" in seen["system"] and "跨年" in r.output
