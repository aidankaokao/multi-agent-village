import sqlite3

import httpx
import pytest

from village.config import Settings
from village.llm.base import ChatResult, LLMClient, LLMError, RemoteModel
from village.main import create_app

SECRET = "sk-test-super-secret-key-0001"


class FakeClient(LLMClient):
    """依 base_url 決定行為：含 'broken' 的一律失敗。記錄收到的 api_key。"""

    seen_keys: list = []

    def __init__(self, kind, base_url, api_key):
        self.base_url, self.api_key = base_url or "", api_key
        FakeClient.seen_keys.append(api_key)

    async def chat(self, model, messages, **kw):
        if "broken" in self.base_url:
            raise LLMError("boom")
        return ChatResult(text=f"hi from {model}", model=model)

    async def list_models(self):
        if "broken" in self.base_url:
            raise LLMError("unreachable")
        return [RemoteModel(id="m-small"), RemoteModel(id="m-large")]


@pytest.fixture
async def env(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY", "VILLAGE_ADMIN_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    db = tmp_path / "v.db"
    settings = Settings(database_url=f"sqlite+aiosqlite:///{db}", config_dir=tmp_path / "cfg")
    app = create_app(settings, client_factory=FakeClient)
    async with app.router.lifespan_context(app):
        token = app.state.ctx.admin_token
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers={"Authorization": f"Bearer {token}"}
        ) as c:
            yield c, db


async def _provider(c, name="cloud", base_url="http://cloud/v1", api_key=SECRET):
    r = await c.post(
        "/api/llm/providers", json={"name": name, "kind": "openai_compat", "base_url": base_url, "api_key": api_key}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_requires_admin_token(env):
    c, _ = env
    r = await c.get("/api/llm/providers", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


async def test_api_key_never_returned_and_encrypted_at_rest(env):
    c, db = env
    p = await _provider(c)
    assert p["has_api_key"] and p["api_key_hint"] == "sk-…0001"
    listing = (await c.get("/api/llm/providers")).text
    assert SECRET not in listing and SECRET not in str(p)

    raw = sqlite3.connect(db).execute("select api_key_ct, api_key_hint from llm_providers").fetchone()
    assert SECRET.encode() not in raw[0]
    assert db.read_bytes().find(SECRET.encode()) == -1


async def test_patch_without_key_keeps_it_and_clear_removes(env):
    c, _ = env
    p = await _provider(c)
    r = await c.patch(f"/api/llm/providers/{p['id']}", json={"name": "renamed"})
    assert r.json()["has_api_key"] and r.json()["name"] == "renamed"

    FakeClient.seen_keys.clear()
    await c.get(f"/api/llm/providers/{p['id']}/remote-models")
    assert FakeClient.seen_keys == [SECRET]  # 解密後只交給 client

    r = await c.patch(f"/api/llm/providers/{p['id']}", json={"clear_api_key": True})
    assert not r.json()["has_api_key"] and r.json()["api_key_hint"] is None


async def test_remote_models_marks_registered_and_reports_failures(env):
    c, _ = env
    p = await _provider(c)
    await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "m-small"})
    r = await c.get(f"/api/llm/providers/{p['id']}/remote-models")
    assert {m["id"]: m["registered"] for m in r.json()} == {"m-large": False, "m-small": True}

    bad = await _provider(c, name="bad", base_url="http://broken/v1")
    r = await c.get(f"/api/llm/providers/{bad['id']}/remote-models")
    assert r.status_code == 502


async def test_tiers_and_fallback(env):
    c, _ = env
    good = await _provider(c, name="local", base_url="http://local/v1", api_key=None)
    bad = await _provider(c, name="cloud", base_url="http://broken/v1")
    m_bad = (await c.post("/api/llm/models", json={"provider_id": bad["id"], "model": "cloud-big"})).json()
    m_good = (await c.post("/api/llm/models", json={"provider_id": good["id"], "model": "qwen-7b"})).json()

    r = await c.put("/api/llm/tiers", json={"large": [m_bad["id"]], "small": [m_good["id"]]})
    assert r.json() == {"large": [m_bad["id"]], "small": [m_good["id"]]}

    # large 失敗 → 自動退到 small
    r = await c.post("/api/llm/try", json={"target": "tier:large", "prompt": "hi"})
    assert r.status_code == 200 and r.json()["model"] == "qwen-7b"

    r = await c.put("/api/llm/tiers", json={"large": ["nope"]})
    assert r.status_code == 422


async def test_delete_provider_cascades_models_and_tiers(env):
    c, _ = env
    p = await _provider(c)
    m = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "x"})).json()
    await c.put("/api/llm/tiers", json={"large": [m["id"]]})
    assert (await c.delete(f"/api/llm/providers/{p['id']}")).status_code == 204
    assert (await c.get("/api/llm/models")).json() == []
    assert (await c.get("/api/llm/tiers")).json() == {"large": [], "small": []}


async def test_truncated_empty_output_falls_back_and_effort_is_passed(env):
    c, _ = env

    class Truncating(FakeClient):
        efforts: list = []

        async def chat(self, model, messages, **kw):
            Truncating.efforts.append((model, kw.get("reasoning_effort")))
            if "thinker" in model:
                raise LLMError("輸出被截斷且沒有內容")
            return await super().chat(model, messages, **kw)

    c._transport.app.state.ctx.llm._factory = Truncating
    p = await _provider(c, name="local", base_url="http://local/v1", api_key=None)
    a = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "thinker"})).json()
    b = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "fast", "reasoning_effort": "none"})).json()
    assert b["reasoning_effort"] == "none"
    await c.put("/api/llm/tiers", json={"small": [a["id"], b["id"]]})

    r = await c.post("/api/llm/try", json={"target": "tier:small", "prompt": "hi"})
    assert r.json()["model"] == "fast"
    assert Truncating.efforts == [("thinker", None), ("fast", "none")]

    r = await c.patch(f"/api/llm/models/{b['id']}", json={"reasoning_effort": "bogus"})
    assert r.status_code == 422


async def test_defaults_endpoint(env):
    c, _ = env
    r = await c.get("/api/llm/defaults")
    assert r.json()["base_urls"]["ollama"].endswith("/v1")
