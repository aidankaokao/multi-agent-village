"""Phase 6 T4-A：駐村專家。讀取名片、邀請入村（token 加密）、更新、重新讀取名片、健康檢查、刪除。"""

import sqlite3

import httpx
import pytest

from village.config import REPO_ROOT, Settings
from village.core import remote
from village.main import create_app

CARD = {
    "protocolVersion": "0.3.0",
    "name": "台灣史專家",
    "description": "回答台灣歷史問題並附上引用來源。",
    "url": "http://localhost:8100/a2a",
    "version": "0.1.0",
    "skills": [{"id": "taiwan_history_qa", "name": "台灣史問答", "description": "回答台灣歷史問題",
                "tags": ["台灣史", "RAG"], "examples": ["霧社事件的起因是什麼？"]}],
    "x-llm-headers": {"X-LLM-API-Key": "…"},
}
TOKEN = "th-token-abcdefghijklmnop"


class FakeAgent:
    """模擬外接 agent：card、health 可以調整成掛掉或還沒準備好。"""

    def __init__(self):
        self.card = dict(CARD)
        self.health = 200
        self.ready = True
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "down.example":
            raise httpx.ConnectError("refused")
        if request.url.path.endswith("/.well-known/agent-card.json"):
            return httpx.Response(200, json=self.card)
        if request.url.path.endswith("/health"):
            return httpx.Response(self.health, json={"ok": self.ready})
        return httpx.Response(404)


@pytest.fixture
async def env(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    agent = FakeAgent()
    monkeypatch.setattr(remote, "_client", lambda timeout=10: httpx.AsyncClient(transport=httpx.MockTransport(agent.handler)))
    db = tmp_path / "v.db"
    settings = Settings(database_url=f"sqlite+aiosqlite:///{db}", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    app = create_app(settings, handoff_delay=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
            yield c, app, agent, db


def test_split_url_and_suggest_id():
    assert remote.split_url("http://host.docker.internal:8100/") == (
        "http://host.docker.internal:8100", "http://host.docker.internal:8100/.well-known/agent-card.json")
    assert remote.split_url("http://h:8100/sub/.well-known/agent-card.json")[0] == "http://h:8100/sub"
    with pytest.raises(remote.RemoteError):
        remote.split_url("host.docker.internal:8100")
    assert remote.suggest_id(CARD, "http://host.docker.internal:8100") == "taiwan-history"
    assert remote.a2a_path(CARD) == "/a2a" and remote.a2a_path({}) == "/a2a"


async def test_preview_invite_and_secrets(env):
    c, app, agent, db = env
    r = await c.post("/api/residents/preview", json={"url": "http://host.docker.internal:8100"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["card"]["name"] == "台灣史專家" and p["suggested_id"] == "taiwan-history" and p["online"] and p["wants_llm"]

    r = await c.post("/api/residents", json={"id": "taiwan-history", "url": p["url"], "token": TOKEN, "sprite": "scholar"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "online" and out["has_token"] and out["token_hint"] and TOKEN not in r.text
    assert out["share_llm"] is False  # 預設關閉
    # 也是村民（kind=remote），出現在名冊與公開清單
    v = (await c.get("/api/villagers")).json()
    spec = next(x for x in v if x["id"] == "taiwan-history")["spec"]
    assert spec["kind"] == "remote" and spec["card"]["name"] == "台灣史專家" and spec["remote"]["a2a_path"] == "/a2a"
    assert spec["card"]["skills"][0]["examples"] == ["霧社事件的起因是什麼？"]
    agents = (await c.get("/api/agents")).json()["agents"]
    assert next(a for a in agents if a["id"] == "taiwan-history")["status"] == "online"
    # token 加密存 DB，明碼不在檔案裡
    raw = sqlite3.connect(db).execute("select token_ct from remote_agents").fetchone()[0]
    assert TOKEN.encode() not in raw and db.read_bytes().find(TOKEN.encode()) == -1
    assert TOKEN not in (await c.get("/api/residents")).text

    assert (await c.post("/api/residents", json={"id": "taiwan-history", "url": p["url"]})).status_code == 409
    assert (await c.post("/api/residents", json={"id": "mayor", "url": p["url"]})).status_code == 422


async def test_update_refresh_and_delete(env):
    c, app, agent, db = env
    await c.post("/api/residents", json={"id": "th", "url": "http://host.docker.internal:8100", "token": TOKEN})
    r = (await c.patch("/api/residents/th", json={"share_llm": True})).json()
    assert r["share_llm"] is True and r["has_token"]
    r = (await c.patch("/api/residents/th", json={"clear_token": True})).json()
    assert not r["has_token"] and r["token_hint"] is None

    # 改名字、外觀走村民 API；重新讀取名片時保留
    spec = next(x for x in (await c.get("/api/villagers")).json() if x["id"] == "th")["spec"]
    spec["card"]["name"], spec["x-village"]["sprite"] = "史學家", "farmer"
    assert (await c.put("/api/villagers/th", json={"spec": spec})).status_code == 200
    agent.card = {**CARD, "description": "新版說明", "url": "http://localhost:8100/rpc"}
    r = (await c.post("/api/residents/th/refresh")).json()
    assert r["a2a_path"] == "/rpc" and r["share_llm"] is True and r["card"]["description"] == "新版說明"
    spec = next(x for x in (await c.get("/api/villagers")).json() if x["id"] == "th")["spec"]
    assert spec["card"]["name"] == "史學家" and spec["x-village"]["sprite"] == "farmer" and spec["card"]["description"] == "新版說明"

    assert (await c.delete("/api/villagers/th")).status_code == 204
    assert (await c.get("/api/residents")).json() == []
    assert sqlite3.connect(db).execute("select count(*) from remote_agents").fetchone()[0] == 0


async def test_health_status_changes_and_events(env):
    from village.api.residents import check_all

    c, app, agent, db = env
    ctx = app.state.ctx
    events: list[dict] = []
    q = ctx.bus.subscribe()
    await c.post("/api/residents", json={"id": "th", "url": "http://host.docker.internal:8100"})
    while not q.empty():
        q.get_nowait()

    agent.ready = False  # 索引還沒建好：當作還不能派工
    await check_all(ctx)
    r = (await c.get("/api/residents")).json()[0]
    assert r["status"] == "offline" and "還沒準備好" in r["last_error"]
    agent.ready, agent.health = True, 404  # 沒有 /health：改讀 agent card
    await check_all(ctx)
    assert (await c.get("/api/residents")).json()[0]["status"] == "online"
    while not q.empty():
        events.append(q.get_nowait())
    assert [(e["type"], e["status"]) for e in events if e["type"] == "residents.status"] == [
        ("residents.status", "offline"), ("residents.status", "online")]

    r = (await c.patch("/api/residents/th", json={"url": "http://down.example:8100"})).json()
    assert r["status"] == "offline" and "連不上" in r["last_error"]


async def test_bad_cards_and_unreachable(env):
    c, app, agent, db = env
    r = await c.post("/api/residents/preview", json={"url": "http://down.example:8100"})
    assert r.status_code == 502 and "連不上" in r.json()["detail"]
    agent.card = {"hello": "world"}
    r = await c.post("/api/residents/preview", json={"url": "http://host.docker.internal:8100"})
    assert r.status_code == 502 and "agent card" in r.json()["detail"]
    r = await c.post("/api/residents/preview", json={"url": "ftp://x"})
    assert r.status_code == 502


async def test_remote_agents_are_not_dispatched_yet(env):
    c, app, agent, db = env
    await c.post("/api/residents", json={"id": "th", "url": "http://host.docker.internal:8100"})
    from village.core.orchestrator import planner_system_prompt

    agents = [a for a in app.state.ctx.registry.all() if a.kind != "remote"]
    assert "th（" not in planner_system_prompt(agents)
    assert app.state.ctx.registry.get("th").kind == "remote"


# ---------------------------------------------------------------- T4-B：派工與 A2A 呼叫

import asyncio  # noqa: E402
import json  # noqa: E402

from village.llm.base import ChatResult, LLMClient, RemoteModel  # noqa: E402

USER_KEY = "sk-user-llm-key-1234567890"
REPLY = {
    "jsonrpc": "2.0", "id": "x",
    "result": {"kind": "message", "role": "agent", "messageId": "m", "parts": [
        {"kind": "text", "text": "霧社事件的導火線是敬酒風波[1]。"},
        {"kind": "data", "data": {"citations": [{"n": 1, "title": "霧社事件", "section": "霧社事件 › 事件近因",
                                                  "url": "https://zh.wikipedia.org/zh-tw/霧社事件"}],
                                  "model": "deepseek/deepseek-v4-flash", "llm_error": None}},
    ]},
}


def _sse(*results):
    return "".join(f"data: {json.dumps({'jsonrpc': '2.0', 'id': 'x', 'result': r}, ensure_ascii=False)}\n\n" for r in results)


_DATA = REPLY["result"]["parts"][1]["data"]
SSE_REPLY = _sse(
    {"kind": "task", "id": "t", "contextId": "c", "status": {"state": "working"}},
    {"kind": "artifact-update", "append": False, "artifact": {"artifactId": "answer", "parts": [{"kind": "text", "text": "霧社事件的導火線"}]}},
    {"kind": "artifact-update", "append": True, "artifact": {"artifactId": "answer", "parts": [{"kind": "text", "text": "是敬酒風波[1]。"}]}},
    {"kind": "artifact-update", "append": False, "lastChunk": True, "artifact": {"artifactId": "answer-data", "parts": [{"kind": "data", "data": _DATA}]}},
    {"kind": "status-update", "status": {"state": "completed"}, "final": True},
)


class PlannerClient(LLMClient):
    """村子這邊的 LLM：規劃時把問題交給駐村專家 th。"""

    def __init__(self, kind, base_url, api_key):
        pass

    async def chat(self, model, messages, **kw):
        if kw.get("json_schema") and "th（" in (kw.get("system") or ""):
            plan = {"steps": [{"id": "s1", "agent": "th", "instruction": "說明霧社事件的起因", "use": []}]}
            return ChatResult(text=json.dumps(plan), model=model)
        if kw.get("json_schema"):
            return ChatResult(text='{"steps":[]}', model=model)
        return ChatResult(text="村長回答", model=model)

    async def list_models(self):
        return [RemoteModel(id="x")]


@pytest.fixture
async def village(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    agent = FakeAgent()

    def handler(request: httpx.Request):
        if request.method == "POST" and request.url.path == "/a2a":
            agent.requests.append(request)
            if agent.health == 401:
                return httpx.Response(401, json={"detail": "bad token"})
            method = json.loads(request.content)["method"]
            mode = agent.card.get("_mode", "json")
            if method == "message/stream" and mode == "sse":
                return httpx.Response(200, text=SSE_REPLY, headers={"content-type": "text/event-stream"})
            if method == "message/stream" and mode == "nostream":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "error": {"code": -32601, "message": "不支援"}})
            return httpx.Response(200, json=REPLY)
        return agent.handler(request)

    monkeypatch.setattr(remote, "_client", lambda timeout=10: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'v.db'}", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    app = create_app(settings, client_factory=PlannerClient, handoff_delay=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
            p = (await c.post("/api/llm/providers", json={"name": "or", "kind": "openai_compat",
                                                          "base_url": "https://openrouter.ai/api/v1", "api_key": USER_KEY})).json()
            m = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "deepseek/deepseek-v4-flash"})).json()
            await c.put("/api/llm/tiers", json={"large": [m["id"]]})
            rules = (await c.get("/api/rules")).json()
            await c.put("/api/rules", json={**rules, "reviewer_enabled": False, "memory_auto": False})
            await c.post("/api/residents", json={"id": "th", "url": "http://host.docker.internal:8100", "token": TOKEN})
            yield c, app, agent


def a2a_calls(agent):
    return [r for r in agent.requests if r.method == "POST" and r.url.path == "/a2a"]


async def _ask(c, question="霧社事件的起因是什麼？"):
    r = (await c.post("/api/chat", json={"message": question})).json()
    for _ in range(200):
        msgs = (await c.get(f"/api/conversations/{r['conversation_id']}/messages")).json()
        if len(msgs) >= 2:
            trace = (await c.get(f"/api/runs/{r['run_id']}")).json()
            return msgs[1], trace
        await asyncio.sleep(0.02)
    raise AssertionError("no answer")


async def test_dispatch_to_resident_without_sharing_llm(village):
    c, app, agent = village
    msg, trace = await _ask(c)
    assert "敬酒風波" in msg["content"] and "資料來源：" in msg["content"] and "《霧社事件》事件近因" in msg["content"]
    [req] = a2a_calls(agent)
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert "x-llm-api-key" not in req.headers and "x-llm-base-url" not in req.headers  # 開關預設關閉：不交出 LLM
    body = json.loads(req.content)
    assert body["method"] == "message/stream" and body["params"]["message"]["parts"][0]["text"] == "說明霧社事件的起因"
    step = trace["steps"][0]
    assert step["agent"] == "th" and step["model"].startswith("deepseek/deepseek-v4-flash（")
    assert TOKEN not in json.dumps(trace) and USER_KEY not in json.dumps(trace)


async def test_share_llm_sends_askers_model(village):
    c, app, agent = village
    await c.patch("/api/residents/th", json={"share_llm": True})
    msg, trace = await _ask(c)
    req = a2a_calls(agent)[-1]
    assert req.headers["x-llm-base-url"] == "https://openrouter.ai/api/v1"
    assert req.headers["x-llm-model"] == "deepseek/deepseek-v4-flash" and req.headers["x-llm-api-key"] == USER_KEY
    assert USER_KEY not in json.dumps(trace) and USER_KEY not in msg["content"]


async def test_offline_resident_is_not_planned_and_errors_are_reported(village):
    from village.api.residents import check_all

    c, app, agent = village
    agent.ready = False
    await check_all(app.state.ctx)
    msg, _ = await _ask(c)
    assert msg["content"] == "村長回答" and a2a_calls(agent) == []  # 外出中：村長名冊裡沒有它

    agent.ready = True
    await check_all(app.state.ctx)
    agent.health = 401  # 對方拒絕 token
    msg, trace = await _ask(c)
    assert "拒絕了存取 token" in trace["steps"][0]["error"]


def test_parse_reply_variants():
    reply = remote.parse_reply(REPLY)
    assert reply.text.startswith("霧社事件") and reply.data["citations"][0]["n"] == 1 and reply.files == []
    task = {"jsonrpc": "2.0", "id": 1, "result": {"kind": "task", "status": {"state": "completed"},
                                                    "artifacts": [{"parts": [{"kind": "text", "text": "任務結果"}]}]}}
    assert remote.parse_reply(task).text == "任務結果"
    from village.llm.base import LLMError

    with pytest.raises(LLMError, match="回報錯誤"):
        remote.parse_reply({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "不支援"}})
    with pytest.raises(LLMError, match="沒有回答內容"):
        remote.parse_reply({"jsonrpc": "2.0", "id": 1, "result": {"kind": "message", "parts": []}})
    assert remote.render_citations({}) == ""
    out = remote.render_citations({"citations": [{"n": 2, "title": "李登輝", "section": "李登輝 › 生平",
                                                  "url": "https://zh.wikipedia.org/zh-tw/%E6%9D%8E%E7%99%BB%E8%BC%9D"}]})
    assert out == "資料來源：\n[2] 《李登輝》生平　https://zh.wikipedia.org/zh-tw/李登輝"



async def test_streaming_reply_is_shown_piece_by_piece(village):
    c, app, agent = village
    agent.card["_mode"] = "sse"
    q = app.state.ctx.bus.subscribe()
    msg, trace = await _ask(c)
    assert msg["content"].startswith("霧社事件的導火線是敬酒風波[1]。") and "資料來源：" in msg["content"]
    deltas = []
    while not q.empty():
        e = q.get_nowait()
        if e["type"] == "agent.delta" and e.get("agent") == "th":
            deltas.append(e["text"])
    # 收到串流片段（顯示事件有節流，太快的片段會合併；完整答案與資料來源由 agent.finished 送到畫面）
    assert deltas and deltas[0] == "霧社事件的導火線"
    assert trace["steps"][0]["model"].startswith("deepseek/deepseek-v4-flash")


async def test_falls_back_to_send_and_remembers(village):
    c, app, agent = village
    remote._NO_STREAM.clear()
    agent.card["_mode"] = "nostream"
    msg, _ = await _ask(c)
    assert "敬酒風波" in msg["content"]
    assert [json.loads(r.content)["method"] for r in a2a_calls(agent)] == ["message/stream", "message/send"]
    await _ask(c)  # 第二次直接用 message/send
    assert [json.loads(r.content)["method"] for r in a2a_calls(agent)][2:] == ["message/send"]
    remote._NO_STREAM.clear()


async def test_stream_request_answered_with_plain_json(village):
    c, app, agent = village
    agent.card["_mode"] = "json"  # 對方不串流、直接回完整 Message：照樣接受
    msg, _ = await _ask(c)
    assert "敬酒風波" in msg["content"] and len(a2a_calls(agent)) == 1
