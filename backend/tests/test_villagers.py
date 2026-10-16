import copy
import sqlite3

import httpx
import pytest

from village.config import REPO_ROOT, Settings
from village.llm.base import ChatResult, LLMClient, LLMError
from village.main import create_app

AGENTS_DIR = REPO_ROOT / "agents"

TRANSLATOR = {
    "card": {
        "id": "translator",
        "name": "翻譯官",
        "description": "中英互譯",
        "skills": [{"id": "translate", "description": "翻譯", "examples": ["翻成英文"]}],
    },
    "x-village": {"sprite": "translator", "home": [15, 14], "keywords": ["翻譯", "英文"]},
    "model": {"tier": "small", "temperature": 0.2},
    "prompt": {"system": "你是翻譯官。只輸出譯文。", "user_template": "任務：{instruction}\n{context}"},
    "validate": {"max_chars": 50},
    "tests": [
        {"input": "把「貓」翻成英文", "expect": {"lang": "en", "contains": ["cat"]}},
        {"input": "把「狗」翻成英文", "expect": {"lang": "en", "contains": ["dog"]}},
    ],
}


class FakeClient(LLMClient):
    """翻譯官：「貓」→ cat；其他都回中文（讓測試題失敗）。長度超過時第二次會縮短。"""

    def __init__(self, kind, base_url, api_key):
        self.base_url = base_url or ""

    async def chat(self, model, messages, **kw):
        if "broken" in self.base_url:
            raise LLMError("boom")
        prompt = messages[0].content
        if len(messages) > 1:  # harness 帶回饋重做
            return ChatResult(text="short", model=model)
        if "很長" in prompt:
            return ChatResult(text="x" * 80, model=model)
        return ChatResult(text="cat" if "貓" in prompt else "狗", model=model)

    async def list_models(self):
        return []


@pytest.fixture
async def env(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY", "VILLAGE_ADMIN_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/v.db", config_dir=tmp_path / "cfg", agents_dir=AGENTS_DIR
    )
    app = create_app(settings, client_factory=FakeClient, handoff_delay=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})  # 第一位 = 管理員
            yield c, app, tmp_path / "v.db"


async def _register_model(c, base_url="http://local/v1"):
    p = (await c.post("/api/llm/providers", json={"name": base_url, "kind": "openai_compat", "base_url": base_url})).json()
    m = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "fake-7b"})).json()
    await c.put("/api/llm/tiers", json={"large": [m["id"]]})
    return m


async def _public_ids(c):
    return [a["id"] for a in (await c.get("/api/agents")).json()["agents"]]


async def test_builtin_seeded_once(env):
    c, app, _ = env
    rows = (await c.get("/api/villagers")).json()
    assert {r["id"] for r in rows} >= {"poet", "jester"}
    assert all(r["builtin"] for r in rows)
    assert rows[0]["spec"]["card"]["id"] == rows[0]["id"] and "x-village" in rows[0]["spec"]


async def test_crud_and_events(env):
    c, app, _ = env
    events = app.state.ctx.bus.subscribe()

    r = await c.post("/api/villagers", json={"spec": TRANSLATOR})
    assert r.status_code == 201, r.text
    assert "translator" in await _public_ids(c)
    assert (await events.get())["type"] == "agents.changed"
    assert app.state.ctx.registry.get("translator").village.home == (15, 14)

    assert (await c.post("/api/villagers", json={"spec": TRANSLATOR})).status_code == 409

    edited = copy.deepcopy(TRANSLATOR)
    edited["card"]["name"] = "翻譯大師"
    r = await c.put("/api/villagers/translator", json={"spec": edited})
    assert r.json()["spec"]["card"]["name"] == "翻譯大師"
    assert app.state.ctx.registry.get("translator").name == "翻譯大師"

    # id 不能改
    edited["card"]["id"] = "other"
    assert (await c.put("/api/villagers/translator", json={"spec": edited})).status_code == 422

    # 停用 → 村子裡消失，但管理清單還在
    await c.patch("/api/villagers/translator", json={"enabled": False})
    assert "translator" not in await _public_ids(c)
    assert "translator" in [r["id"] for r in (await c.get("/api/villagers")).json()]

    assert (await c.delete("/api/villagers/translator")).status_code == 204
    assert "translator" not in [r["id"] for r in (await c.get("/api/villagers")).json()]
    assert (await c.delete("/api/villagers/translator")).status_code == 404


async def test_validation_messages(env):
    c, _, _ = env
    bad = copy.deepcopy(TRANSLATOR)
    bad["card"]["id"] = "Bad Id"
    bad["prompt"]["user_template"] = "沒有任務"
    r = await c.post("/api/villagers", json={"spec": bad})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "card.id" in detail and "{instruction}" in detail

    mayor = copy.deepcopy(TRANSLATOR)
    mayor["card"]["id"] = "mayor"
    r = await c.post("/api/villagers", json={"spec": mayor})
    assert r.status_code == 422 and "保留" in r.json()["detail"]


async def test_deleted_builtin_stays_deleted_until_restored(env):
    c, app, _ = env
    assert (await c.delete("/api/villagers/poet")).status_code == 204
    assert "poet" not in await _public_ids(c)

    # 重啟（重新匯入內建範例）也不會跑回來
    from village.store.agents import seed_builtin

    assert await seed_builtin(app.state.ctx.sessionmaker, AGENTS_DIR) == []

    r = await c.post("/api/villagers/restore-builtin")
    assert r.json()["restored"] == ["poet"]
    assert "poet" in await _public_ids(c)


async def test_yaml_roundtrip(env):
    c, _, _ = env
    text = (await c.post("/api/villagers/to-yaml", json={"spec": TRANSLATOR})).json()["yaml"]
    assert "翻譯官" in text and text.startswith("card:")
    spec = (await c.post("/api/villagers/from-yaml", json={"yaml": text})).json()["spec"]
    assert spec["card"]["id"] == "translator" and spec["tests"][0]["expect"]["lang"] == "en"

    r = await c.post("/api/villagers/from-yaml", json={"yaml": "card: [oops"})
    assert r.status_code == 422 and "YAML" in r.json()["detail"]


async def test_try_and_tests(env):
    c, _, _ = env
    await _register_model(c)

    # 草稿不用先儲存就能試打
    r = (await c.post("/api/villagers/try", json={"spec": TRANSLATOR, "instruction": "把「貓」翻成英文"})).json()
    assert r["ok"] and r["output"] == "cat" and r["model"] == "fake-7b" and r["attempts"] == 1

    # 測試題：期望條件
    case = TRANSLATOR["tests"][1]
    r = (await c.post("/api/villagers/try", json={"spec": TRANSLATOR, "instruction": case["input"], "expect": case["expect"]})).json()
    assert not r["ok"] and r["error"] is None
    assert any("英文" in f for f in r["failures"]) and any("dog" in f for f in r["failures"])

    # harness 一樣會檢查長度並帶回饋重做
    r = (await c.post("/api/villagers/try", json={"spec": TRANSLATOR, "instruction": "寫很長的東西"})).json()
    assert r["ok"] and r["attempts"] == 2 and r["output"] == "short" and "太長" in r["retry_reasons"][0]


async def test_try_reports_llm_errors(env):
    c, _, _ = env
    await _register_model(c, base_url="http://broken/v1")
    r = (await c.post("/api/villagers/try", json={"spec": TRANSLATOR, "instruction": "翻譯"})).json()
    assert not r["ok"] and "boom" in r["error"]


async def test_specs_stored_as_json(env):
    c, _, db = env
    await c.post("/api/villagers", json={"spec": TRANSLATOR})
    raw = sqlite3.connect(db).execute("select spec from agents where id='translator'").fetchone()[0]
    assert '"x-village"' in raw and '"tests"' in raw
