"""視覺模型：訊息帶圖片的格式、看圖測驗（出題 / 批改）、vision 分級和一般分級分開。"""

import base64
import json
import random
import struct
import zlib

import httpx
import pytest

from village.config import Settings
from village.llm import vision_test
from village.llm.base import ChatMessage, ChatResult, ImagePart, LLMClient, RemoteModel
from village.main import create_app

QUIZ = vision_test.VisionQuiz(number="4729", color="藍", image=ImagePart("image/png", "AAAA"))


class FakeClient(LLMClient):
    """模型名稱含 vlm 的看得到圖（照答案回答）；其他的忽略圖片亂答。記錄收到的訊息。"""

    seen: list = []

    def __init__(self, kind, base_url, api_key):
        pass

    async def chat(self, model, messages, **kw):
        FakeClient.seen.append((model, messages))
        if messages[-1].images and "vlm" in model:
            return ChatResult(text="數字 4729，藍色", model=model)
        return ChatResult(text="數字 1234，紅色", model=model)

    async def list_models(self):
        return [RemoteModel(id="x")]


@pytest.fixture
async def env(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY", "VILLAGE_ADMIN_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(vision_test, "make_quiz", lambda rng=None: QUIZ)
    FakeClient.seen = []
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'v.db'}", config_dir=tmp_path / "cfg")
    app = create_app(settings, client_factory=FakeClient)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
            p = (await c.post("/api/llm/providers", json={"name": "or", "kind": "openai_compat", "base_url": "http://or/v1"})).json()
            ids = {}
            for name in ("vlm-mini", "text-only"):
                ids[name] = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": name})).json()["id"]
            yield c, ids


# ---------------------------------------------------------------- 出題 / 批改


def _decode_png(png: bytes) -> tuple[int, int, bytes]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", (0, 0)
    while pos < len(png):
        (n,) = struct.unpack(">I", png[pos : pos + 4])
        kind, data = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + n]
        assert struct.unpack(">I", png[pos + 8 + n : pos + 12 + n])[0] == zlib.crc32(kind + data) & 0xFFFFFFFF
        if kind == b"IHDR":
            size = struct.unpack(">II", data[:8])
        elif kind == b"IDAT":
            idat += data
        pos += 12 + n
    return size[0], size[1], zlib.decompress(idat)


def test_quiz_image_is_valid_png_with_the_shape_color():
    quiz = vision_test.make_quiz(random.Random(1))
    assert len(quiz.number) == 4 and quiz.color in vision_test.COLORS
    w, h, raw = _decode_png(base64.b64decode(quiz.image.data))
    assert (w, h) == (vision_test.W, vision_test.H) and len(raw) == h * (1 + w * 3)
    row = raw[(h // 2) * (1 + w * 3) + 1 :]  # 中間那一列，圖形的中心點
    assert tuple(row[80 * 3 : 80 * 3 + 3]) == vision_test.COLORS[quiz.color][0]
    assert quiz.image.data_url.startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    ("answer", "passed"),
    [
        ("數字 4729，藍色", True),
        ("The number is 4,729 and the circle is blue.", True),
        ("數字 4 7 2 9，藍色", True),
        ("數字 4728，藍色", False),
        ("數字 4729，紅色", False),
        ("數字 4729，可能是藍色或紫色", False),  # 同時說好幾個顏色不算
        ("看不到圖片", False),
    ],
)
def test_grade(answer, passed):
    assert vision_test.grade(QUIZ, answer).passed is passed


# ---------------------------------------------------------------- 送出格式


async def test_openai_compat_sends_images_as_content_parts():
    from village.llm.openai_compat import OpenAICompatClient

    bodies: list[dict] = []

    def handler(request: httpx.Request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    client = OpenAICompatClient("http://x/v1", None)
    client._http = httpx.AsyncClient(base_url="http://x/v1/", transport=httpx.MockTransport(handler))
    img = ImagePart("image/jpeg", "QUJD")
    await client.chat("m", [ChatMessage("user", "前一輪"), ChatMessage("user", "看這張", images=[img])])
    msgs = bodies[0]["messages"]
    assert msgs[0]["content"] == "前一輪"  # 沒圖片的維持純文字
    assert msgs[1]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
        {"type": "text", "text": "看這張"},
    ]
    await client.aclose()


def test_anthropic_image_blocks():
    from village.llm.anthropic_client import _content

    m = ChatMessage("user", "看這張", images=[ImagePart("image/png", "QUJD")])
    assert _content(m) == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
        {"type": "text", "text": "看這張"},
    ]
    assert _content(ChatMessage("user", "hi")) == "hi"


# ---------------------------------------------------------------- API


async def test_vision_test_marks_model_and_gates_the_vision_tier(env):
    c, ids = env
    # 還沒測驗：不能放進 vision 分級
    r = await c.put("/api/llm/tiers", json={"vision": [ids["vlm-mini"]]})
    assert r.status_code == 422 and "看圖測驗" in r.json()["detail"]

    r = (await c.post(f"/api/llm/models/{ids['vlm-mini']}/vision-test")).json()
    assert r["passed"] and r["model"]["vision_verified_at"] and r["expected_number"] == "4729"
    assert r["image"].startswith("data:image/png;base64,")
    _, messages = FakeClient.seen[-1]
    assert messages[0].images == [QUIZ.image]

    r = (await c.post(f"/api/llm/models/{ids['text-only']}/vision-test")).json()
    assert not r["passed"] and r["model"]["vision_verified_at"] is None and "看不到" in r["reason"]

    r = await c.put("/api/llm/tiers", json={"vision": [ids["vlm-mini"]]})
    assert r.status_code == 200 and r.json()["vision"] == [ids["vlm-mini"]]
    r = await c.put("/api/llm/tiers", json={"vision": [ids["text-only"]]})
    assert r.status_code == 422


async def test_vision_tier_is_separate_from_general_tiers(env):
    c, ids = env
    await c.post(f"/api/llm/models/{ids['vlm-mini']}/vision-test")
    # 只有一般分級：vision 不會借用 large / small
    await c.put("/api/llm/tiers", json={"large": [ids["text-only"]]})
    r = await c.post("/api/llm/try", json={"target": "tier:vision", "prompt": "hi"})
    assert r.status_code == 502 and "視覺模型" in r.json()["detail"]

    # 只有 vision：一般問答不會用到視覺模型
    await c.put("/api/llm/tiers", json={"vision": [ids["vlm-mini"]]})
    r = await c.post("/api/llm/try", json={"target": "tier:large", "prompt": "hi"})
    assert r.status_code == 502 and "large / small" in r.json()["detail"]
    r = await c.post("/api/llm/try", json={"target": "tier:vision", "prompt": "hi"})
    assert r.status_code == 200 and r.json()["model"] == "vlm-mini"

    # 重測沒過 → vision 分級自動略過它（分級裡還留著，畫面提醒重測）
    QUIZ.number = "1111"
    try:
        await c.post(f"/api/llm/models/{ids['vlm-mini']}/vision-test")
    finally:
        QUIZ.number = "4729"
    r = await c.post("/api/llm/try", json={"target": "tier:vision", "prompt": "hi"})
    assert r.status_code == 502


async def test_vision_test_on_disabled_model_and_other_users_model(env):
    c, ids = env
    await c.patch(f"/api/llm/models/{ids['vlm-mini']}", json={"enabled": False})
    assert (await c.post(f"/api/llm/models/{ids['vlm-mini']}/vision-test")).status_code == 409
    assert (await c.post("/api/llm/models/not-mine/vision-test")).status_code == 404
