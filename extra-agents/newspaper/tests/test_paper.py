import json

import pytest

from app import paper as pp
from app.config import Settings
from app.llm import LLMConfig, LLMError
from app.material import ReadError, read, split_sources
from app.render import cite


def test_material_sources_relabelled(files):
    ms = read(files, Settings().limits, 6000)
    assert [m.label for m in ms] == ["A", "B"]
    a, b = ms
    assert "[A1]" in a.text and "[A2]" in a.text and "資料來源" not in a.text
    assert [(s.id, s.title, s.url) for s in a.sources] == [
        ("A1", "每日科技要聞 capitalfutures.com.tw・2026-10-18", "https://example.com/a"),
        ("A2", "資安週報 ithome.com.tw・2026-10-08", "https://example.com/b"),
    ]
    assert "[B1]" in b.text and b.title == "台股"
    body, found = split_sources("沒有來源的資料 [3]")
    assert found == [] and body == "沒有來源的資料 [3]"


def test_material_limits():
    lim = Settings().limits
    with pytest.raises(ReadError, match="沒有收到採訪資料"):
        read([], lim, 100)
    with pytest.raises(ReadError, match="格式不支援"):
        read([("a.pdf", b"x")], lim, 100)
    with pytest.raises(ReadError, match="最多"):
        read([("a.txt", b"x")] * 7, lim, 100)
    [m] = read([("a.txt", ("很長" * 5000).encode())], lim, 100)
    assert m.truncated and m.text.endswith("（後略）")


def test_cite_markup_is_escaped():
    assert str(cite("<b>漲[1][2]</b>")) == '&lt;b&gt;漲<sup class="cite">1,2</sup>&lt;/b&gt;'


DESK_AI = {"section": "科技", "articles": [
    {"title": "國產大模型攻克數學難題", "lead": "國內大模型攻克數學難題[A1]。", "body": ["支出達 2.52 兆美元[A2][A9]。"], "sources": ["A1", "Z9"]},
    {"title": "臺灣網路威脅升至第 4", "lead": "一年 193 起駭客活動[A2]。", "body": ["估計損失 999 億元。"], "sources": ["A2"]},
], "briefs": [{"title": "資安快訊", "text": "威脅增加[A2]", "sources": ["A2"]}]}
DESK_STOCK = {"section": "財經", "articles": [
    {"title": "台股單周重挫 2,202 點", "lead": "跌幅 4.47%[B1]。", "body": [], "sources": ["B1"]}], "briefs": []}
FRONT = {"edition": "科技・財經早報", "headline": 3, "subtitle": "地緣政治與油價拖累", "editorial": "本期聚焦台股[B1]。"}


def fake_chat(answers):
    calls = []

    async def chat(cfg, system, user, **kw):
        calls.append((system, user))
        key = "front" if "總編輯" in system else ("ai" if "資料 A" in user else "stock")
        ans = answers[key]
        if isinstance(ans, Exception):
            raise ans
        return json.dumps(ans, ensure_ascii=False), "fake-model"

    return chat, calls


LLM = LLMConfig("http://localhost:11434/v1", "m")


async def test_two_stage_paper(files, monkeypatch):
    chat, calls = fake_chat({"ai": DESK_AI, "stock": DESK_STOCK, "front": FRONT})
    monkeypatch.setattr(pp, "chat", chat)
    seen = []

    async def progress(m):
        seen.append(m)

    paper, pdf, png = await pp.run(files, "科技與財經早報", LLM, Settings(), progress)
    assert pdf.startswith(b"%PDF") and png.startswith(b"\x89PNG")
    assert paper.edition == "科技・財經早報" and paper.model == "fake-model" and paper.llm_error is None
    # 第 3 則（台股）當頭條：來源從頭條開始編號
    assert paper.headline.title == "台股單周重挫 2,202 點" and paper.headline.subtitle == "地緣政治與油價拖累"
    assert paper.headline.lead == "跌幅 4.47%[1]。" and paper.headline.sources == [1]
    a1 = paper.articles[0]
    assert a1.section == "科技" and a1.lead == "國內大模型攻克數學難題[2]。"
    assert a1.body == ["支出達 2.52 兆美元[3]。"] and a1.sources == [2, 3]  # 不存在的 A9、Z9 拿掉
    assert [s.url for s in paper.sources] == ["https://example.com/c", "https://example.com/a", "https://example.com/b"]
    assert paper.editorial == "本期聚焦台股[1]。"
    assert [x for x, _ in paper.sections] == ["科技"] and len(paper.briefs) == 1
    assert paper.unverified == ["「臺灣網路威脅升至第 4」的 999"]  # 資料裡沒有的數字
    assert "今天是" in calls[0][1] and "[A1]" in calls[0][1]
    assert seen[-1] == "排版中" and "記者寫稿：2 / 2 份資料" in seen
    md = paper.to_markdown()
    assert "頭條：台股單周重挫" in md and "[1]" not in md
    d = paper.to_dict()
    assert d["citations"][0] == {"n": 1, "title": "台股週報 news.cnyes.com", "url": "https://example.com/c"}


async def test_fallbacks(files, monkeypatch):
    chat, _ = fake_chat({"ai": LLMError("壞掉"), "stock": DESK_STOCK, "front": LLMError("頭版壞掉")})
    monkeypatch.setattr(pp, "chat", chat)
    paper, _, _ = await pp.run(files, "", LLM, Settings())
    # AI 那份照小標題排；頭版失敗就第一則當頭條
    assert paper.headline.title == "國產大模型" and paper.edition == "新聞彙整"
    assert "壞掉" in paper.llm_error and "頭版壞掉" in paper.llm_error
    assert [a.title for a in paper.articles] == ["資安", "台股單周重挫 2,202 點"]
    # 沒有 LLM
    paper, _, _ = await pp.run(files, "", None, Settings())
    assert paper.llm_error == "沒有提供 LLM" and paper.headline.sources == [1, 2]


@pytest.mark.real_render
async def test_real_layout(files):
    pytest.importorskip("pypdfium2")
    try:
        import weasyprint  # noqa: F401
    except OSError:
        pytest.skip("沒有 Pango（macOS 本機要設 DYLD_FALLBACK_LIBRARY_PATH）")
    from app.render import page_count

    paper, pdf, png = await pp.run(files, "", None, Settings())
    assert pdf.startswith(b"%PDF") and page_count(pdf) >= 1 and png.startswith(b"\x89PNG")
