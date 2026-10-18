import base64
import json

import httpx
import pytest

from app import paper as pp
from app.config import Settings
from app.main import create_app


@pytest.fixture
async def client(monkeypatch):
    app = create_app(Settings(access_tokens=["t0k"]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://np") as c:
        yield c


def rpc(method, parts):
    return {"jsonrpc": "2.0", "id": 7, "method": method,
            "params": {"message": {"kind": "message", "role": "user", "messageId": "m", "parts": parts}}}


def file_part(name, data):
    return {"kind": "file", "file": {"name": name, "mimeType": "text/plain", "bytes": base64.b64encode(data).decode()}}


AUTH = {"Authorization": "Bearer t0k"}


async def test_card_and_auth(client, files):
    card = (await client.get("/.well-known/agent-card.json")).json()
    assert card["name"] == "報社" and card["url"].endswith(":8103/a2a")
    assert card["x-file-limits"]["extensions"] == [".txt", ".md"] and card["x-file-limits"]["maxFiles"] == 6
    assert "網路研究員" in card["description"]
    body = rpc("message/send", [file_part(*files[0])])
    assert (await client.post("/a2a", json=body)).status_code == 401
    r = (await client.post("/a2a", json=rpc("message/send", [{"kind": "text", "text": "做報紙"}]), headers=AUTH)).json()
    assert "沒有收到採訪資料" in r["error"]["message"]


async def test_send_and_stream(client, files):
    parts = [{"kind": "text", "text": "科技早報"}] + [file_part(n, d) for n, d in files]
    r = (await client.post("/a2a", json=rpc("message/send", parts), headers=AUTH)).json()
    out = r["result"]["parts"]
    assert [p["kind"] for p in out] == ["text", "file", "file", "data"]
    assert out[1]["file"]["mimeType"] == "application/pdf" and out[2]["file"]["mimeType"] == "image/png"
    assert out[1]["file"]["name"].startswith("村莊日報_新聞彙整_") and out[3]["data"]["llm_error"] == "沒有提供 LLM"
    assert out[3]["data"]["citations"][0]["url"] == "https://example.com/a"

    async with client.stream("POST", "/a2a", json=rpc("message/stream", parts), headers=AUTH) as resp:
        events = [json.loads(line[5:]) async for line in resp.aiter_lines() if line.startswith("data:")]
    kinds = [e["result"]["kind"] for e in events]
    assert kinds[0] == "task" and kinds[-1] == "status-update" and events[-1]["result"]["final"]
    texts = [e["result"]["status"]["message"]["parts"][0]["text"] for e in events if e["result"]["kind"] == "status-update" and not e["result"]["final"]]
    assert texts[0] == "整理 2 份採訪資料" and "排版中" in texts
    ids = [e["result"]["artifact"]["artifactId"] for e in events if e["result"]["kind"] == "artifact-update"]
    assert ids == ["paper", "paper-pdf", "paper-preview", "paper-data"]


async def test_bad_llm_host(client, files):
    h = {**AUTH, "X-LLM-Base-URL": "http://10.0.0.5/v1", "X-LLM-Model": "m"}
    r = await client.post("/a2a", json=rpc("message/send", [file_part(*files[0])]), headers=h)
    assert r.status_code == 400 and "不允許的 LLM 主機" in r.json()["detail"]
