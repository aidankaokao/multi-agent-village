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
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c, db


async def _provider(c, name="cloud", base_url="http://cloud/v1", api_key=SECRET):
    r = await c.post(
        "/api/llm/providers", json={"name": name, "kind": "openai_compat", "base_url": base_url, "api_key": api_key}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_no_admin_token_by_default(env):
    c, _ = env
    r = await c.get("/api/llm/providers", headers={"Authorization": ""})
    assert r.status_code == 200


async def test_admin_token_enforced_when_set(env):
    c, _ = env
    c._transport.app.state.ctx.admin_token = "s3cret-token"
    r = await c.get("/api/llm/providers", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    r = await c.get("/api/llm/providers", headers={"Authorization": "Bearer s3cret-token"})
    assert r.status_code == 200


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


async def test_streaming_restarts_on_fallback(env):
    c, _ = env

    class Streaming(FakeClient):
        async def chat(self, model, messages, on_delta=None, **kw):
            for piece in ("半", "途"):
                await on_delta(piece)
            if "broken" in self.base_url:
                raise LLMError("斷線")
            return ChatResult(text="半途", model=model)

    ctx = c._transport.app.state.ctx
    ctx.llm._factory = Streaming
    good = await _provider(c, name="local", base_url="http://local/v1", api_key=None)
    bad = await _provider(c, name="cloud", base_url="http://broken/v1")
    m_bad = (await c.post("/api/llm/models", json={"provider_id": bad["id"], "model": "big"})).json()
    m_good = (await c.post("/api/llm/models", json={"provider_id": good["id"], "model": "small"})).json()
    await c.put("/api/llm/tiers", json={"large": [m_bad["id"], m_good["id"]]})

    seen: list[str] = []

    async def on_text(text):
        seen.append(text)

    r = await ctx.llm.chat("tier:large", [], on_text=on_text)
    assert r.model == "small"
    assert seen == ["半", "半途", "半", "半途"]  # 換模型後從頭累積，不會接在失敗的半成品後面


async def test_openai_compat_sse_parsing():
    from village.llm.base import ChatMessage
    from village.llm.openai_compat import OpenAICompatClient

    def sse(*chunks):
        body = "".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    def handler(request: httpx.Request):
        import json

        body = json.loads(request.content)
        assert body["stream"] is True
        if body["model"] == "thinker":  # 只有思考、沒有內容就被截斷
            return sse('{"model":"thinker","choices":[{"delta":{"reasoning":"嗯"},"finish_reason":"length"}]}')
        if body["model"] == "down":
            return httpx.Response(500, text="server exploded")
        return sse(
            '{"model":"m","choices":[{"delta":{"role":"assistant","content":""}}]}',
            '{"model":"m","choices":[{"delta":{"content":"你"}}]}',
            ": keep-alive",
            '{"model":"m","choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}',
        )

    client = OpenAICompatClient("http://x/v1", None)
    client._http = httpx.AsyncClient(base_url="http://x/v1/", transport=httpx.MockTransport(handler))
    pieces: list[str] = []

    async def on_delta(p):
        pieces.append(p)

    r = await client.chat("m", [ChatMessage("user", "hi")], on_delta=on_delta)
    assert (r.text, r.model, pieces) == ("你好", "m", ["你", "好"])
    with pytest.raises(LLMError, match="截斷"):
        await client.chat("thinker", [ChatMessage("user", "hi")], on_delta=on_delta)
    with pytest.raises(LLMError, match="HTTP 500"):
        await client.chat("down", [ChatMessage("user", "hi")], on_delta=on_delta)
    await client.aclose()


async def test_openai_compat_adapts_unsupported_params():
    """模擬 gpt-5：不收 max_tokens / temperature / reasoning_effort=none，組織未驗證不能串流。"""
    import json

    from village.llm import openai_compat
    from village.llm.base import ChatMessage
    from village.llm.openai_compat import OpenAICompatClient

    calls: list[dict] = []

    def bad(param: str, code: str = "unsupported_parameter"):
        err = {"error": {"message": f"{param} bad", "type": "invalid_request_error", "param": param, "code": code}}
        return httpx.Response(400, json=err)

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        if "max_tokens" in body:
            return bad("max_tokens")
        if "temperature" in body:
            return bad("temperature", "unsupported_value")
        if body.get("reasoning_effort") == "none":
            return bad("reasoning_effort", "unsupported_value")
        if body.get("stream"):
            return bad("stream", "unsupported_value")
        return httpx.Response(200, json={"model": "gpt-5", "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]})

    openai_compat._QUIRKS.clear()
    client = OpenAICompatClient("http://x/v1", None)
    client._http = httpx.AsyncClient(base_url="http://x/v1/", transport=httpx.MockTransport(handler))
    pieces: list[str] = []

    async def on_delta(p):
        pieces.append(p)

    kw = dict(max_tokens=100, temperature=0.2, reasoning_effort="none", on_delta=on_delta)
    r = await client.chat("gpt-5", [ChatMessage("user", "hi")], **kw)
    assert r.text == "hi" and pieces == ["hi"]  # 不能串流時一次給完
    final = calls[-1]
    assert final["max_completion_tokens"] == 100 and final["reasoning_effort"] == "minimal"
    assert "temperature" not in final and "stream" not in final and len(calls) == 5

    calls.clear()  # 記住了：第二次直接成功
    await client.chat("gpt-5", [ChatMessage("user", "hi")], **kw)
    assert len(calls) == 1

    # gpt-4o-mini：param 是 null，只在訊息裡說不認得 reasoning_effort → 直接拿掉
    def handler_4o(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        if "reasoning_effort" in body:
            msg = "Unrecognized request argument supplied: reasoning_effort"
            return httpx.Response(400, json={"error": {"message": msg, "type": "invalid_request_error", "param": None, "code": None}})
        return httpx.Response(200, json={"model": "gpt-4o-mini", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    calls.clear()
    client._http = httpx.AsyncClient(base_url="http://x/v1/", transport=httpx.MockTransport(handler_4o))
    r = await client.chat("gpt-4o-mini", [ChatMessage("user", "hi")], max_tokens=10, temperature=0.2, reasoning_effort="none")
    assert r.text == "ok" and len(calls) == 2
    assert "reasoning_effort" not in calls[-1] and calls[-1]["temperature"] == 0.2 and calls[-1]["max_tokens"] == 10

    # 其他 400（不是參數問題）照常失敗，不會無限重試
    def handler2(request):
        return httpx.Response(400, json={"error": {"message": "bad key", "code": "invalid_api_key"}})

    client._http = httpx.AsyncClient(base_url="http://x/v1/", transport=httpx.MockTransport(handler2))
    with pytest.raises(LLMError, match="HTTP 400"):
        await client.chat("other", [ChatMessage("user", "hi")], max_tokens=10)
    await client.aclose()
