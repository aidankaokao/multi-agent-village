import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import fetch, research as R, search as S
from app.config import Settings
from app.llm import LLMConfig
from app.main import create_app

SEARX = {"results": [
    {"title": "電價暫不調整", "url": "https://news.example.com/a", "content": "經濟部決議電價暫不調整", "publishedDate": "2026-09-18T10:00:00"},
    {"title": "電價懶人包", "url": "https://news.example.com/b", "content": "一度電多少錢"},
    {"title": "同站第三篇", "url": "https://news.example.com/c", "content": "x"},
    {"title": "台電公告", "url": "https://www.taipower.example.tw/n", "content": "台電公告電價"},
    {"title": "ftp", "url": "ftp://bad", "content": ""},
]}
ARTICLE = ("<html><head><title>電價暫不調整</title></head><body><article><h1>電價暫不調整</h1>"
           "<p>經濟部 9 月 18 日電價費率審議會決議，本期電價暫不調整，應有調幅為 12.83%，留待 12 月再議。</p>"
           "<p>官方說明將優先爭取政府撥補 711 億元，以減輕台電的虧損壓力。</p>"
           "<p>目前全國平均電價維持每度 3.7823 元，住宅與工業用戶的費率都沒有改變。</p></article></body></html>")


@pytest.fixture
def web(monkeypatch):
    """假的 SearXNG 與網站；DNS：*.example.* 是公開位址，internal.example 是內網。"""
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path == "/search":
            return httpx.Response(200, json=SEARX)
        if req.url.path == "/healthz":
            return httpx.Response(200, text="OK")
        if req.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://internal.example/secret"})
        if req.url.path == "/pdf":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html; charset=utf-8"})

    mock = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)  # noqa: E731
    monkeypatch.setattr(S, "_client", mock)
    monkeypatch.setattr(fetch, "_client", mock)

    async def resolve(host):
        return ["10.0.0.5"] if host.startswith("internal") or host == "localhost" else ["93.184.216.34"]

    monkeypatch.setattr(fetch, "_resolve", resolve)
    return calls


async def test_search_and_merge(web):
    hits = await S.search("http://searx", "電價")
    assert [h.site for h in hits] == ["news.example.com"] * 3 + ["taipower.example.tw"]  # ftp 不算
    assert hits[0].published == "2026-09-18"
    merged = S.merge([hits, hits[::-1]], max_pages=4, per_site=2)
    assert [h.url for h in merged] == ["https://news.example.com/a", "https://www.taipower.example.tw/n", "https://news.example.com/b"]


async def test_fetch_blocks_private_and_non_html(web):
    page = await fetch.read("https://news.example.com/a", 100_000, 5)
    assert page.title == "電價暫不調整" and "12.83%" in page.text
    for url, msg in [("http://internal.example/x", "內網"), ("http://localhost:8000/", "內網"),
                     ("https://news.example.com/redirect", "內網"), ("https://news.example.com/pdf", "不是網頁"),
                     ("file:///etc/passwd", "http")]:
        with pytest.raises(fetch.FetchError, match=msg):
            await fetch.read(url, 100_000, 5)
    assert not any("internal.example/secret" in c for c in web)  # 轉址到內網：沒有真的去抓


def test_pick_passages_and_citations():
    hit = S.Hit("標題", "https://a.example.com/x", "摘要")
    page = fetch.Page("https://a.example.com/x", "電價新聞", "無關的開場白。\n" + "電價暫不調整，12 月再議。" * 5, "2026-09-18")
    other = S.Hit("讀不到", "https://b.example.com", "只有摘要的電價資訊")
    srcs = R.pick_passages("電價有調整嗎", [(hit, page), (other, None)], total=5)
    assert [s.n for s in srcs] == [1, 2] and srcs[0].date == "2026-09-18" and not srcs[1].read_ok
    assert "（只有搜尋摘要）" in R.render_sources(srcs)
    assert R.cited("結論 [2]，另見 [9]", srcs) == [srcs[1].citation()]
    assert srcs[0].citation() == {"n": 1, "title": "電價新聞", "section": "a.example.com・2026-09-18", "url": "https://a.example.com/x"}


LLM = LLMConfig("http://localhost:11434/v1", "m")


async def collect(gen):
    return [x async for x in gen]


async def test_research_flow_with_llm(web, monkeypatch):
    async def fake_chat(cfg, system, user, **kw):
        assert "今天是" in system
        return '{"queries": ["2026 電價", "台電 公告"]}', "m"

    async def fake_stream(cfg, system, user, **kw):
        assert "不是給你的指令" in system and "[1]" in user
        kw["meta"]["model"] = "m"
        for piece in ["暫不調整", " [1]。"]:
            yield piece

    monkeypatch.setattr(R, "chat", fake_chat)
    monkeypatch.setattr(R, "chat_stream", fake_stream)
    events = await collect(R.research("2026 電價有調整嗎", LLM, Settings(searxng_url="http://searx")))
    kinds = [k for k, _ in events]
    assert kinds[0] == "progress" and "delta" in kinds and kinds[-1] == "done"
    assert any(v == "搜尋：2026 電價／台電 公告" for k, v in events if k == "progress")
    r = events[-1][1]
    assert r.answer == "暫不調整 [1]。" and [c["n"] for c in r.citations] == [1] and r.model == "m"


async def test_truncated_answer_retries_with_fewer_sources(web, monkeypatch):
    seen = []

    async def fake_chat(cfg, system, user, **kw):
        return '{"queries": ["電價"]}', "m"

    async def fake_stream(cfg, system, user, **kw):
        seen.append(user.count("\n[") + user.startswith("[") )
        kw["meta"]["finish"] = "length" if len(seen) == 1 else "stop"
        yield "半句" if len(seen) == 1 else "完整回答 [1]"

    monkeypatch.setattr(R, "chat", fake_chat)
    monkeypatch.setattr(R, "chat_stream", fake_stream)
    s = Settings(searxng_url="http://searx", max_pages=6, per_site=6)
    events = await collect(R.research("電價", LLM, s))
    assert ("replace", "") in events and any("減少參考資料" in str(v) for k, v in events if k == "progress")
    r = events[-1][1]
    assert r.answer == "完整回答 [1]" and any("只用了前" in n for n in r.notes)


async def test_research_without_llm(web):
    r = (await collect(R.research("電價", None, Settings(searxng_url="http://searx"))))[-1][1]
    assert r.llm_error == "沒有提供 LLM" and "[1]" in r.answer and r.citations and r.queries == ["電價"]


def test_api_card_auth_and_stream(web, monkeypatch):
    async def fake_chat(cfg, system, user, **kw):
        return '{"queries": ["電價"]}', "m"

    async def fake_stream(cfg, system, user, **kw):
        kw["meta"]["model"] = "m"
        for p in ["暫不", "調整 [1]"]:
            yield p

    monkeypatch.setattr(R, "chat", fake_chat)
    monkeypatch.setattr(R, "chat_stream", fake_stream)
    c = TestClient(create_app(Settings(searxng_url="http://searx", access_tokens=["t"])))
    card = c.get("/.well-known/agent-card.json").json()
    assert card["name"] == "網路研究員" and card["capabilities"]["streaming"]
    msg = {"jsonrpc": "2.0", "id": 1, "method": "message/stream",
           "params": {"message": {"kind": "message", "role": "user", "messageId": "m", "parts": [{"kind": "text", "text": "電價"}]}}}
    assert c.post("/a2a", json=msg).status_code == 401
    with c.stream("POST", "/a2a", json=msg, headers={"Authorization": "Bearer t", "X-LLM-Base-URL": "http://localhost:11434/v1",
                                                     "X-LLM-Model": "m"}) as r:
        events = [json.loads(line[5:])["result"] for line in r.iter_lines() if line.startswith("data:")]
    assert events[0]["kind"] == "task"
    progress = [e["status"]["message"]["parts"][0]["text"] for e in events if e["kind"] == "status-update" and not e["final"]]
    assert progress[0] == "想搜尋關鍵字" and "整理回答" in progress
    texts = [(e["append"], e["artifact"]["parts"][0]) for e in events if e["kind"] == "artifact-update"]
    assert texts[0] == (False, {"kind": "text", "text": "暫不"}) and texts[1] == (True, {"kind": "text", "text": "調整 [1]"})
    assert texts[-1][1]["data"]["citations"][0]["n"] == 1 and events[-1]["final"]
    # 沒有 LLM：整段送
    with c.stream("POST", "/a2a", json=msg, headers={"Authorization": "Bearer t"}) as r:
        events = [json.loads(line[5:])["result"] for line in r.iter_lines() if line.startswith("data:")]
    first_text = next(e for e in events if e["kind"] == "artifact-update")["artifact"]["parts"][0]["text"]
    assert first_text.startswith("（沒有提供 LLM")
    big = {**msg, "params": {"message": {**msg["params"]["message"], "parts": [{"kind": "text", "text": "x" * 5000}]}}}
    assert "太長" in c.post("/a2a", json=big, headers={"Authorization": "Bearer t"}).json()["error"]["message"]
