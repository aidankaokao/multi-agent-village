"""R2/R3：問答附文件 → 交給收文件的駐村專家（A2A file part）→ 收回報告 PDF、存起來、只有本人能下載。"""

import asyncio
import base64
import json

import httpx
import pytest

from village.config import REPO_ROOT, Settings
from village.core import docfiles, remote
from village.llm.base import ChatResult, LLMClient, RemoteModel
from village.main import create_app

CARD = {
    "protocolVersion": "0.3.0", "name": "文件彙整師", "description": "把文件整理成彙整報告 PDF。",
    "url": "http://localhost:8101/a2a", "version": "0.1.0", "capabilities": {"streaming": True},
    "defaultInputModes": ["application/pdf", "text/csv"],
    "skills": [{"id": "document_report", "name": "文件彙整報告", "description": "讀取附上的文件，整理成彙整報告 PDF",
                "inputModes": ["application/pdf"]}],
    "x-file-limits": {"extensions": [".xlsx", ".csv", ".txt", ".pdf"], "maxFiles": 3},
}
PDF = b"%PDF-1.4 fake report"


def _sse(*results):
    return "".join(f"data: {json.dumps({'jsonrpc': '2.0', 'id': 'x', 'result': r}, ensure_ascii=False)}\n\n" for r in results)


REPORT_SSE = _sse(
    {"kind": "task", "id": "t", "contextId": "c", "status": {"state": "working"}},
    {"kind": "status-update", "final": False, "status": {"state": "working", "message": {
        "kind": "message", "role": "agent", "parts": [{"kind": "text", "text": "讀取檔案 1/2：a.txt"}]}}},
    {"kind": "artifact-update", "append": False, "artifact": {"artifactId": "report", "parts": [{"kind": "text", "text": "**營運彙整**\n\n總結…"}]}},
    {"kind": "artifact-update", "append": False, "artifact": {"artifactId": "report-pdf", "parts": [
        {"kind": "file", "file": {"name": "營運彙整.pdf", "mimeType": "application/pdf", "bytes": base64.b64encode(PDF).decode()}}]}},
    {"kind": "artifact-update", "append": False, "lastChunk": True, "artifact": {"artifactId": "report-data", "parts": [
        {"kind": "data", "data": {"model": "gpt-5-mini", "llm_error": None}}]}},
    {"kind": "status-update", "status": {"state": "completed"}, "final": True},
)


class FakeAgent:
    def __init__(self):
        self.card = dict(CARD)
        self.calls: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/agent-card.json"):
            return httpx.Response(200, json=self.card)
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST" and request.url.path == "/a2a":
            self.calls.append(json.loads(request.content))
            return httpx.Response(200, text=REPORT_SSE, headers={"content-type": "text/event-stream"})
        return httpx.Response(404)


class Planner(LLMClient):
    """規劃時：系統提示裡有「收文件」且 plan_docs 開著 → 派給 rm；否則回空（測試村長漏派時的補救）。"""

    plan_docs = True

    def __init__(self, kind, base_url, api_key):
        pass

    async def chat(self, model, messages, **kw):
        system = kw.get("system") or ""
        if kw.get("json_schema"):
            Planner.last_system = system
            if Planner.plan_docs and "收文件" in system and ("附了文件" in system or "附過文件" in system):
                return ChatResult(text=json.dumps({"steps": [{"id": "s1", "agent": "rm", "instruction": "整理成報告", "use": []}]}), model=model)
            return ChatResult(text='{"steps":[]}', model=model)
        return ChatResult(text="村長回答", model=model)

    async def list_models(self):
        return [RemoteModel(id="x")]


@pytest.fixture
async def village(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    agent = FakeAgent()
    Planner.plan_docs = True
    monkeypatch.setattr(remote, "_client", lambda timeout=10: httpx.AsyncClient(transport=httpx.MockTransport(agent.handler)))
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'v.db'}", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    app = create_app(settings, client_factory=Planner, handoff_delay=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
            p = (await c.post("/api/llm/providers", json={"name": "or", "kind": "openai_compat",
                                                          "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-x"})).json()
            m = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "m"})).json()
            await c.put("/api/llm/tiers", json={"large": [m["id"]]})
            rules = (await c.get("/api/rules")).json()
            await c.put("/api/rules", json={**rules, "reviewer_enabled": False, "memory_auto": False})
            yield c, app, agent


def f(name: str, data: bytes) -> dict:
    return {"name": name, "data": base64.b64encode(data).decode()}


async def _wait(c, r, n=2):
    for _ in range(300):
        msgs = (await c.get(f"/api/conversations/{r['conversation_id']}/messages")).json()
        if len(msgs) >= n and msgs[-1]["role"] == "assistant":
            return msgs
        await asyncio.sleep(0.02)
    raise AssertionError("no answer")


def test_check_and_accepts():
    assert docfiles.check("../../a.PDF", b"%PDF-1.7").name == "a.PDF"
    with pytest.raises(docfiles.DocError, match="另存成 .xlsx"):
        docfiles.check("a.xls", b"x")
    with pytest.raises(docfiles.DocError, match="不是有效的 PDF"):
        docfiles.check("a.pdf", b"hello")
    with pytest.raises(docfiles.DocError, match="不是有效的 xlsx"):
        docfiles.check("a.xlsx", b"hello")
    with pytest.raises(docfiles.DocError, match="不是文字檔"):
        docfiles.check("a.txt", b"\x00\x01binary")
    with pytest.raises(docfiles.DocError, match="最多附 3 份"):
        docfiles.check_all([("a.txt", b"x")] * 4)
    assert docfiles.accepts_from_card(CARD) == [".xlsx", ".csv", ".txt", ".pdf"]
    assert docfiles.accepts_from_card({"defaultInputModes": ["text/plain", "application/pdf"]}) == [".pdf"]
    assert docfiles.accepts_from_card({"defaultInputModes": ["text/plain"]}) == []  # 一般文字訊息不算收 txt


async def test_files_need_a_document_resident(village):
    c, app, agent = village
    r = await c.post("/api/chat", json={"message": "整理", "files": [f("a.txt", "內容".encode())]})
    assert r.status_code == 409 and "沒有能處理文件的駐村專家" in r.json()["detail"]
    r = await c.post("/api/chat", json={"message": "整理", "files": [f("a.docx", b"PK")]})
    assert r.status_code == 422 and "格式不支援" in r.json()["detail"]


async def test_report_flow(village):
    c, app, agent = village
    inv = await c.post("/api/residents", json={"id": "rm", "url": "http://host.docker.internal:8101"})
    assert inv.status_code == 201, inv.text
    agents = (await c.get("/api/agents")).json()["agents"]
    assert next(a for a in agents if a["id"] == "rm")["accepts"] == [".xlsx", ".csv", ".txt", ".pdf"]
    q = app.state.ctx.bus.subscribe()

    r = (await c.post("/api/chat", json={"message": "", "files": [f("a.txt", "會議紀錄".encode()), f("b.pdf", b"%PDF-1.4 x")]})).json()
    assert [x["name"] for x in r["files"]] == ["a.txt", "b.pdf"]
    msgs = await _wait(c, r)
    user, answer = msgs[0], msgs[1]
    assert user["content"] == "請彙整這些文件的重點" and [x["name"] for x in user["meta"]["files"]] == ["a.txt", "b.pdf"]
    assert "營運彙整" in answer["content"]
    [att] = answer["meta"]["attachments"]
    assert att["kind"] == "file" and att["name"] == "營運彙整.pdf" and att["url"].startswith("/api/files/")

    # 送出的 A2A 訊息：文字＋兩個 file part
    parts = agent.calls[0]["params"]["message"]["parts"]
    assert [p["kind"] for p in parts] == ["text", "file", "file"]
    assert base64.b64decode(parts[2]["file"]["bytes"]) == b"%PDF-1.4 x" and parts[1]["file"]["mimeType"] == "text/plain"
    assert "附了文件" in Planner.last_system and "收文件" in Planner.last_system

    # 進度（status-update）先顯示在畫面上
    deltas = []
    while not q.empty():
        e = q.get_nowait()
        if e["type"] == "agent.delta" and e.get("agent") == "rm":
            deltas.append(e["text"])
    assert deltas and deltas[0].startswith("⏳ 讀取檔案 1/2")

    # 下載：本人可以、別人不行
    d = await c.get(att["url"])
    assert d.status_code == 200 and d.content == PDF and d.headers["content-type"] == "application/pdf"
    assert "attachment" in d.headers["content-disposition"] and "%E7%87%9F" in d.headers["content-disposition"]
    upload_id = user["meta"]["files"][0]["id"]
    assert (await c.get(f"/api/files/{upload_id}")).content == "會議紀錄".encode()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as eve:
        await eve.post("/api/auth/register", json={"username": "eve", "password": "password123"})
        assert (await eve.get(att["url"])).status_code == 404
        assert (await eve.get(f"/api/files/{upload_id}")).status_code == 404

    # 追問（沒有再附）：前一輪的文件再交給文件彙整師；村長（弱模型）沒派也會補上
    Planner.plan_docs = False
    r2 = (await c.post("/api/chat", json={"message": "第二段寫詳細一點", "conversation_id": r["conversation_id"]})).json()
    msgs = await _wait(c, r2, 4)
    assert "營運彙整" in msgs[3]["content"]
    trace = (await c.get(f"/api/runs/{r2['run_id']}")).json()
    assert any("追問上一份報告" in n for n in trace["plan"]["notes"])
    assert "前面的對話附過文件" in Planner.last_system
    parts = agent.calls[1]["params"]["message"]["parts"]
    assert [p["kind"] for p in parts] == ["text", "file", "file"]

    # 和報告無關的追問：不會再交給文件彙整師
    r3 = (await c.post("/api/chat", json={"message": "你好", "conversation_id": r["conversation_id"]})).json()
    msgs = await _wait(c, r3, 6)
    assert msgs[5]["content"] == "村長回答" and len(agent.calls) == 2


async def test_mayor_forgets_document_agent(village):
    c, app, agent = village
    await c.post("/api/residents", json={"id": "rm", "url": "http://host.docker.internal:8101"})
    Planner.plan_docs = False  # 村長（弱模型）沒派給文件彙整師
    r = (await c.post("/api/chat", json={"message": "幫我看看", "files": [f("a.csv", b"a,b\n1,2\n")]})).json()
    msgs = await _wait(c, r)
    assert "營運彙整" in msgs[1]["content"] and len(agent.calls) == 1
    trace = (await c.get(f"/api/runs/{r['run_id']}")).json()
    assert any("交給文件彙整師" in n for n in trace["plan"]["notes"])


async def test_report_expires(village):
    c, app, agent = village
    store = app.state.ctx.conversations
    fid = await store.save_report((await c.get("/api/auth/status")).json()["user"]["id"], None, "r.pdf", "application/pdf", PDF)
    from datetime import UTC, datetime, timedelta

    from village.store.models import ChatFile

    async with app.state.ctx.sessionmaker() as s:
        row = await s.get(ChatFile, fid)
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        await s.commit()
    assert (await c.get(f"/api/files/{fid}")).status_code == 404
    assert await store.purge_expired() == 1
