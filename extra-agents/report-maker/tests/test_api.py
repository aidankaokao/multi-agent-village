import base64
import json

import pytest
from fastapi.testclient import TestClient

import app.render
from app.config import Settings
from app.main import create_app

AUTH = {"Authorization": "Bearer secret"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app.render, "render_pdf", lambda r: b"%PDF-fake")
    return TestClient(create_app(Settings(access_tokens=["secret"])))


def msg(parts, method="message/send"):
    return {"jsonrpc": "2.0", "id": 7, "method": method,
            "params": {"message": {"kind": "message", "role": "user", "messageId": "m1", "contextId": "c1", "parts": parts}}}


def file_part(name, data: bytes):
    return {"kind": "file", "file": {"name": name, "mimeType": "text/plain", "bytes": base64.b64encode(data).decode()}}


def test_card_and_health(client):
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "文件彙整師" and card["capabilities"]["streaming"]
    assert "application/pdf" in card["defaultInputModes"] and card["x-file-limits"]["maxFiles"] == 3
    assert client.get("/health").json()["auth_configured"] is True


def test_auth_required(client):
    assert client.post("/a2a", json=msg([])).status_code == 401


def test_send_returns_pdf(client):
    r = client.post("/a2a", headers=AUTH, json=msg([{"kind": "text", "text": "整理"}, file_part("a.txt", "會議紀錄\n決議事項".encode())]))
    res = r.json()["result"]
    text, file, data = res["parts"]
    assert res["contextId"] == "c1" and text["kind"] == "text"
    assert file["file"]["mimeType"] == "application/pdf" and base64.b64decode(file["file"]["bytes"]) == b"%PDF-fake"
    assert file["file"]["name"].endswith(".pdf")
    assert data["data"]["documents"][0]["name"] == "a.txt" and data["data"]["llm_error"] == "沒有提供 LLM"


def test_send_errors(client):
    def err(parts):
        return client.post("/a2a", headers=AUTH, json=msg(parts)).json()["error"]["message"]

    assert "沒有收到文件" in err([{"kind": "text", "text": "hi"}])
    assert "不支援 uri" in err([{"kind": "file", "file": {"name": "a.txt", "uri": "http://x"}}])
    assert "base64" in err([{"kind": "file", "file": {"name": "a.txt", "bytes": "%%%"}}])
    assert "格式不支援" in err([file_part("a.docx", b"x")])
    assert "最多 3 份" in err([file_part(f"{i}.txt", b"x") for i in range(4)])


def test_body_too_large(client):
    big = Settings(access_tokens=["secret"])
    big.limits.max_total_bytes = 1000
    c = TestClient(create_app(big))
    r = c.post("/a2a", headers=AUTH, json=msg([file_part("a.txt", b"x" * 2_000_000)]))
    assert r.status_code == 413


def test_llm_url_allowlist(client):
    r = client.post("/a2a", headers={**AUTH, "X-LLM-Base-URL": "http://10.0.0.5/v1", "X-LLM-Model": "m"},
                    json=msg([file_part("a.txt", b"hello")]))
    assert r.status_code == 400 and "不允許" in r.json()["detail"]


def test_stream_events(client):
    with client.stream("POST", "/a2a", headers=AUTH, json=msg([file_part("a.txt", "內容".encode())], "message/stream")) as r:
        events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    results = [e["result"] for e in events]
    assert results[0]["kind"] == "task"
    progress = [x["status"]["message"]["parts"][0]["text"] for x in results if x["kind"] == "status-update" and not x["final"]]
    assert progress[0] == "讀取檔案 1/1：a.txt" and progress[-1] == "產生 PDF"
    arts = [x["artifact"]["parts"][0]["kind"] for x in results if x["kind"] == "artifact-update"]
    assert arts == ["text", "file", "data"]
    assert results[-1]["final"] and results[-1]["status"]["state"] == "completed"


def test_stream_read_error(client):
    with client.stream("POST", "/a2a", headers=AUTH, json=msg([file_part("a.pdf", b"bad")], "message/stream")) as r:
        events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert "不是有效的 PDF" in events[-1]["error"]["message"]
