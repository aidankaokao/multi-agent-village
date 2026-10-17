import json

import pytest

from app import report as R
from app.config import Limits, Settings
from app.llm import LLMConfig, LLMError, parse_json
from app.readers import read

LLM = LLMConfig("http://localhost:11434/v1", "fake")


def settings(**kw) -> Settings:
    s = Settings(access_tokens=["t"])
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def good_json(names: list[str]) -> str:
    return json.dumps({
        "title": "銷售彙整", "summary": "第一段\n\n第二段",
        "files": [{"name": n, "points": ["重點一"]} for n in names],
        "comparison": ["差異"], "conclusions": ["結論"], "recommendations": [],
        "key_figures": [{"label": "總金額", "value": "8,000 元", "source": names[0]},
                        {"label": "成長率", "value": "37.5%", "source": names[0]}],  # 37.5 不在資料裡 → 移除
    }, ensure_ascii=False)


def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('好的，報告如下：{"a": 2} 以上') == {"a": 2}
    with pytest.raises(LLMError):
        parse_json("沒有 JSON")


def test_chunks_split_long_text():
    parts = R._chunks("甲" * 25 + "\n" + "乙" * 5, 10)
    assert all(len(p) <= 10 for p in parts) and "".join(parts).replace("\n", "") == "甲" * 25 + "乙" * 5


async def test_compose_with_llm(monkeypatch, sales_xlsx):
    docs = [read("銷售.xlsx", sales_xlsx, Limits())]
    calls = []

    async def fake_chat(cfg, system, user, **kw):
        calls.append((system, user, kw))
        return good_json(["銷售.xlsx"]), "fake-model"

    monkeypatch.setattr(R, "chat", fake_chat)
    rep = await R.compose(docs, "著重部門", LLM, settings())
    assert rep.title == "銷售彙整" and rep.model == "fake-model" and rep.llm_error is None
    assert "依「部門」分組小計" in calls[0][1] and "著重部門" in calls[0][1]
    assert calls[0][2]["schema"] is R.SCHEMA
    assert rep.comparison == []  # 只有一份文件：不放跨檔比較
    assert [k["value"] for k in rep.key_figures] == ["8,000 元"]
    assert any("對不上" in n for n in rep.notes)
    md = rep.to_markdown()
    assert "**銷售彙整**" in md and "- 結論" in md


async def test_compose_retries_then_falls_back(monkeypatch, sales_xlsx):
    docs = [read("銷售.xlsx", sales_xlsx, Limits())]
    n = 0

    async def bad_chat(cfg, system, user, **kw):
        nonlocal n
        n += 1
        return "抱歉我不會", "fake"

    monkeypatch.setattr(R, "chat", bad_chat)
    rep = await R.compose(docs, "整理", LLM, settings())
    assert n == 2  # 重試一次
    assert rep.title.endswith("（統計版）") and "JSON" in rep.llm_error
    assert any("金額總和 8,000" in p for p in rep.files[0]["points"])


async def test_compose_without_llm(sales_xlsx):
    rep = await R.compose([read("銷售.xlsx", sales_xlsx, Limits())], "整理", None, settings())
    assert rep.llm_error == "沒有提供 LLM" and rep.model is None


async def test_long_text_is_summarized_in_chunks(monkeypatch):
    doc = read("長文.txt", ("這是一段很長的內容。" * 30 + "\n").encode() * 10, Limits())
    seen = []

    async def fake_chat(cfg, system, user, **kw):
        seen.append(user)
        if kw.get("schema"):
            return good_json(["長文.txt"]), "fake"
        return "- 重點", "fake"

    monkeypatch.setattr(R, "chat", fake_chat)
    progress = []

    async def on_progress(m):
        progress.append(m)

    rep = await R.compose([doc], "整理", LLM, settings(doc_chars=1000), on_progress)
    summaries = [u for u in seen if u.startswith("文件：長文.txt")]
    assert len(summaries) == 4  # 10 段、每段 300 字；一份最多 1,000 字 → 3+3+3+1
    assert "分段摘要" in seen[-1] and any("分段摘要" in n for n in rep.notes)
    assert progress[-1] == "撰寫報告中" and "摘要「長文.txt」：4/4 段" in progress


async def test_run_reports_unreadable_files(monkeypatch, sales_xlsx):
    import app.render

    monkeypatch.setattr(app.render, "render_pdf", lambda r: b"%PDF-fake")
    rep, pdf = await R.run([("銷售.xlsx", sales_xlsx), ("壞.pdf", b"nope")], "", None, settings())
    assert pdf == b"%PDF-fake" and len(rep.docs) == 1
    assert rep.failed[0]["name"] == "壞.pdf" and rep.question == "請彙整這些文件的重點"
    with pytest.raises(R.ReadError):
        await R.run([("壞.pdf", b"nope")], "", None, settings())
