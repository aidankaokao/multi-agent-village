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
    monkeypatch.setattr(remote, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(agent.handler)))
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
