"""Phase 6G-A：生圖模型。OpenRouter /images 格式、image 分級獨立、生圖測驗、畫作 14 天後過期。"""

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from village.config import Settings
from village.llm.base import ChatResult, GeneratedImage, ImageResult, LLMClient, LLMError, RemoteModel
from village.llm.openai_compat import OpenAICompatClient
from village.main import create_app
from village.store.models import ChatImage

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


# ---------------------------------------------------------------- OpenRouter 格式


async def test_openrouter_images_api():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.path.endswith("/images/models"):
            return httpx.Response(200, json={"data": [{"id": "google/gemini-3.1-flash-image", "name": "Nano"}]})
        body = {"model": "google/gemini-3.1-flash-image", "data": [{"b64_json": base64.b64encode(PNG).decode(), "media_type": "image/png"}],
                "usage": {"cost": 0.039}}
        return httpx.Response(200, json=body)

    client = OpenAICompatClient("https://openrouter.ai/api/v1", "k")
    client._http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1/", transport=httpx.MockTransport(handler))
    models = await client.list_image_models()
    assert [m.id for m in models] == ["google/gemini-3.1-flash-image"]
    r = await client.generate_image("google/gemini-3.1-flash-image", "a cat", aspect_ratio="16:9")
    assert r.images[0].data == PNG and r.images[0].mime == "image/png" and r.cost == 0.039
    req = seen[-1]
    assert req.url.path == "/api/v1/images"
    assert json.loads(req.content) == {"model": "google/gemini-3.1-flash-image", "prompt": "a cat", "n": 1, "aspect_ratio": "16:9"}
    await client.aclose()


async def test_openrouter_empty_result_is_an_error():
    client = OpenAICompatClient("http://x/v1", None)
    client._http = httpx.AsyncClient(base_url="http://x/v1/", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})))
    with pytest.raises(LLMError, match="沒有回傳圖片"):
        await client.generate_image("m", "a cat")
    await client.aclose()


# ---------------------------------------------------------------- API


class FakeClient(LLMClient):
    """模型名稱含 svg 的回傳向量圖、含 broken 的失敗，其他回傳 PNG。"""

    def __init__(self, kind, base_url, api_key):
        pass

    async def chat(self, model, messages, **kw):
        return ChatResult(text=f"hi from {model}", model=model)

    async def list_models(self):
        return [RemoteModel(id="text-model")]

    async def list_image_models(self):
        return [RemoteModel(id="img-model")]

    async def generate_image(self, model, prompt, *, aspect_ratio=None, references=None):
        if "broken" in model:
            raise LLMError("content policy")
        data, mime = (SVG, "image/svg+xml") if "svg" in model else (PNG, "image/png")
        return ImageResult(images=[GeneratedImage(mime, data)], model=model, cost=0.04)


@pytest.fixture
async def env(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'v.db'}", config_dir=tmp_path / "cfg")
    app = create_app(settings, client_factory=FakeClient)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
            p = (await c.post("/api/llm/providers", json={"name": "or", "kind": "openai_compat", "base_url": "http://or/v1"})).json()
            ids = {"text": (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "chat-model"})).json()["id"]}
            for name in ("img-model", "svg-model", "broken-model"):
                r = await c.post("/api/llm/models", json={"provider_id": p["id"], "model": name, "output": "image"})
                ids[name] = r.json()["id"]
            yield c, app, p, ids


async def test_list_image_models(env):
    c, _, p, _ = env
    r = (await c.get(f"/api/llm/providers/{p['id']}/remote-models?output=image")).json()
    assert r == [{"id": "img-model", "display_name": None, "context_window": None, "registered": True}]


async def test_image_test_and_tier_rules(env):
    c, app, _, ids = env
    # 還沒測驗：不能放 image 分級
    r = await c.put("/api/llm/tiers", json={"image": [ids["img-model"]]})
    assert r.status_code == 422 and "生圖測驗" in r.json()["detail"]

    r = (await c.post(f"/api/llm/models/{ids['img-model']}/image-test")).json()
    assert r["passed"] and r["model"]["image_verified_at"] and r["cost"] == 0.04 and r["check"] is None
    img = await c.get(r["image_url"])
    assert img.status_code == 200 and img.content == PNG and img.headers["content-type"] == "image/png"

    r = (await c.post(f"/api/llm/models/{ids['svg-model']}/image-test")).json()
    assert not r["passed"] and "SVG" in r["reason"] and r["image_url"] is None
    r = (await c.post(f"/api/llm/models/{ids['broken-model']}/image-test")).json()
    assert not r["passed"] and "content policy" in r["reason"]
    assert (await c.post(f"/api/llm/models/{ids['text']}/image-test")).status_code == 422  # 不是生圖模型

    # 分級規則：生圖模型只能放 image；image 只能放生圖模型
    assert (await c.put("/api/llm/tiers", json={"image": [ids["img-model"]]})).status_code == 200
    r = await c.put("/api/llm/tiers", json={"large": [ids["img-model"]]})
    assert r.status_code == 422 and "只能放進 image" in r.json()["detail"]
    r = await c.put("/api/llm/tiers", json={"image": [ids["text"]]})
    assert r.status_code == 422 and "只能放生圖模型" in r.json()["detail"]

    # 生圖測驗的畫作：沒有對話、14 天後到期
    async with app.state.ctx.sessionmaker() as s:
        row = (await s.scalars(select(ChatImage))).one()
        assert row.kind == "generated" and row.conversation_id is None
        exp = row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
        assert timedelta(days=13) < exp - datetime.now(UTC) <= timedelta(days=14)


async def test_image_models_never_used_for_chat(env):
    c, app, _, ids = env
    await c.put("/api/llm/tiers", json={"large": [ids["text"]]})
    from village.llm.service import llm_user

    llm = app.state.ctx.llm
    me = (await c.get("/api/auth/status")).json()["user"]["id"]
    with llm_user(me):
        assert [m.model for m in await llm.candidates("tier:large", me)] == ["chat-model"]
        assert await llm.candidates("tier:image", me) == []  # 沒測驗過
        with pytest.raises(LLMError, match="生圖模型不能拿來對話"):
            await llm.chat(ids["img-model"], [])  # 指定模型 id 也不行
        with pytest.raises(LLMError, match="生圖模型"):
            await llm.generate_image("tier:image", "a cat")  # 沒有可用的生圖模型
    assert not await llm.has_image(me)


async def test_generated_images_expire(env):
    c, app, _, _ = env
    store = app.state.ctx.conversations
    me = (await c.get("/api/auth/status")).json()["user"]["id"]
    [fresh, old] = await store.save_generated(me, None, [("image/png", PNG), ("image/png", PNG)])
    async with app.state.ctx.sessionmaker() as s:
        (await s.get(ChatImage, old)).expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    assert (await c.get(f"/api/images/{old}")).status_code == 404  # 過期了，即使還沒清掉
    assert (await c.get(f"/api/images/{fresh}")).status_code == 200
    assert await store.purge_expired() == 1
    async with app.state.ctx.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(ChatImage)) == 1


async def test_count_generated_since(env):
    c, app, _, _ = env
    store = app.state.ctx.conversations
    me = (await c.get("/api/auth/status")).json()["user"]["id"]
    await store.save_generated(me, None, [("image/png", PNG)] * 2)
    since = datetime.now(UTC) - timedelta(days=1)
    assert await store.count_generated_since(me, since) == 2
    assert await store.count_generated_since(me, datetime.now(UTC) + timedelta(minutes=1)) == 0



async def test_input_references_sent_as_data_urls():
    from village.llm.base import ImagePart

    seen: list = []

    def handler(request: httpx.Request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    client = OpenAICompatClient("http://x/v1", None)
    client._http = httpx.AsyncClient(base_url="http://x/v1/", transport=httpx.MockTransport(handler))
    await client.generate_image("m", "pixel art", references=[ImagePart("image/jpeg", "QUJD")])
    assert seen[0]["input_references"] == [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]
    await client.generate_image("m", "a cat")
    assert "input_references" not in seen[1]
    await client.aclose()


async def test_last_painting_and_reference_choice(env):
    from village.api.chat import _references

    c, app, _, _ = env
    store = app.state.ctx.conversations
    me = (await c.get("/api/auth/status")).json()["user"]["id"]
    conv = (await store.start_turn(me, None, "畫一隻貓")).conversation_id
    [pid] = await store.save_generated(me, conv, [("image/png", PNG)])
    await store.finish_turn(conv, "r1", "畫好了", "done", {"attachments": [{"kind": "image", "url": f"/api/images/{pid}"}]})
    painting = await store.last_painting(conv, me)
    assert painting == ("image/png", PNG)
    assert await store.last_painting(conv, "someone-else") is None

    upload = [("image/jpeg", b"\xff\xd8\xff")]
    assert [r.source for r in _references(upload, painting, [])] == ["upload"]  # 這次有附圖：用附圖
    refs = _references([], painting, upload)
    assert [r.source for r in refs] == ["painting"] and refs[0].data == PNG  # 修改上一張畫
    assert [r.source for r in _references([], None, upload)] == ["upload"]  # 鑑定師重看的圖
    assert _references([], None, []) == []
