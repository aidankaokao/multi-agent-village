"""Phase 5V-B：問答附圖。格式 / 大小 / 張數把關、沒有視覺模型時提醒、只有本人看得到、跟著對話刪除。"""

import base64
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from test_conversations import _wait_answer, app  # noqa: F401
from test_villagers import _register_model
from village.api.images import sniff
from village.store.models import ChatImage, LlmModel

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def b64(data: bytes, prefix: bool = False) -> str:
    s = base64.b64encode(data).decode()
    return f"data:image/jpeg;base64,{s}" if prefix else s


def test_sniff():
    assert sniff(PNG) == "image/png" and sniff(JPEG) == "image/jpeg"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp" and sniff(b"GIF89a...") == "image/gif"
    assert sniff(b"<svg xmlns=...>") is None and sniff(b"%PDF-1.7") is None


async def _verify_vision(app, model_id):  # noqa: F811
    """等同在介面上通過看圖測驗並放進 vision 分級。"""
    async with app.state.ctx.sessionmaker() as s:
        m = await s.get(LlmModel, model_id)
        m.vision_verified_at = datetime.now(UTC)
        await s.commit()


@pytest.fixture
async def clients(app):  # noqa: F811
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c, \
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as eve:
        await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
        await eve.post("/api/auth/register", json={"username": "eve", "password": "password123"})
        m = await _register_model(c)
        rules = (await c.get("/api/rules")).json()
        await c.put("/api/rules", json={**rules, "reviewer_enabled": False, "memory_auto": False})
        yield c, eve, m


async def test_images_need_a_vision_model(app, clients):  # noqa: F811
    c, _, m = clients
    assert (await c.get("/api/auth/status")).json()["vision_ready"] is False
    r = await c.post("/api/chat", json={"message": "這是什麼", "images": [{"data": b64(JPEG)}]})
    assert r.status_code == 409 and "視覺模型" in r.json()["detail"]

    await _verify_vision(app, m["id"])
    await c.put("/api/llm/tiers", json={"large": [m["id"]], "vision": [m["id"]]})
    st = (await c.get("/api/auth/status")).json()
    assert st["vision_ready"] is True and st["max_images"] == 3


async def test_upload_store_view_and_delete_with_conversation(app, clients):  # noqa: F811
    c, eve, m = clients
    await _verify_vision(app, m["id"])
    await c.put("/api/llm/tiers", json={"large": [m["id"]], "vision": [m["id"]]})

    r = await c.post("/api/chat", json={"message": "", "images": [{"data": b64(JPEG, prefix=True)}, {"data": b64(PNG)}]})
    assert r.status_code == 202, r.text
    body = r.json()
    ids, conv = body["image_ids"], body["conversation_id"]
    assert len(ids) == 2
    msgs = await _wait_answer(c, conv, 2)
    assert msgs[0]["content"] == "這張圖片裡有什麼？"  # 只有附圖沒打字：用預設問題
    assert msgs[0]["meta"] == {"images": ids}

    r = await c.get(f"/api/images/{ids[0]}")
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert "private" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
    assert (await c.get(f"/api/images/{ids[1]}")).headers["content-type"] == "image/png"

    # 別人（包括沒登入）看不到
    assert (await eve.get(f"/api/images/{ids[0]}")).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as anon:
        assert (await anon.get(f"/api/images/{ids[0]}")).status_code == 401

    # 刪對話 → 圖片一起刪
    assert (await c.delete(f"/api/conversations/{conv}")).status_code == 204
    assert (await c.get(f"/api/images/{ids[0]}")).status_code == 404
    async with app.state.ctx.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(ChatImage)) == 0


async def test_upload_validation(app, clients):  # noqa: F811
    c, _, m = clients
    await _verify_vision(app, m["id"])
    await c.put("/api/llm/tiers", json={"large": [m["id"]], "vision": [m["id"]]})

    async def post(images, message="看圖"):
        return await c.post("/api/chat", json={"message": message, "images": [{"data": d} for d in images]})

    r = await post([b64(b"<svg onload=alert(1)>")])
    assert r.status_code == 422 and "不是支援的圖片" in r.json()["detail"]
    r = await post(["!!not base64!!"])
    assert r.status_code == 422 and "格式不對" in r.json()["detail"]
    r = await post([b64(JPEG)] * 4)
    assert r.status_code == 422 and "最多附 3 張" in r.json()["detail"]
    r = await post([b64(b"\xff\xd8\xff" + b"\x00" * (2 * 1024 * 1024))])
    assert r.status_code == 422
    assert (await post([], message="  ")).status_code == 422  # 沒打字也沒附圖

    rules = (await c.get("/api/rules")).json()
    await c.put("/api/rules", json={**rules, "max_images": 0})
    r = await post([b64(JPEG)])
    assert r.status_code == 422 and "不開放附圖" in r.json()["detail"]
    r = await post([])
    assert r.status_code == 202  # 純文字照常
    await _wait_answer(c, r.json()["conversation_id"], 2)
