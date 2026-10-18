"""中轉站：步驟產生的檔案放進中轉站（取件號碼），後面收文件的駐村專家要用時才取件，交給它（A2A file part）。"""

import base64
import json
import shutil

import httpx
import pytest

from test_documents import FakeAgent, _wait
from village.config import REPO_ROOT, Settings
from village.core import remote
from village.core.depot import Depot, slip
from village.core.executor import with_typist
from village.core.orchestrator import Plan, Step
from village.llm import audio_test
from village.llm.base import ChatResult, LLMClient, RemoteModel, Segment, Transcript
from village.main import create_app


async def test_depot_parcels():
    stored = {"f1": b"transcript"}

    async def load(fid):
        return stored.get(fid)

    d = Depot(load)
    [p1, _] = d.drop_attachments("typist", "聽打員", "s1", [
        {"kind": "file", "name": "會議_逐字稿.txt", "url": "/api/files/f1"},
        {"kind": "image", "name": "x.png", "url": "/api/images/i1"},  # 圖片不放
        {"kind": "file", "name": "會議_字幕.srt", "url": "/api/files/f2"},
    ])
    assert (p1.tag, p1.mime, p1.file_id) == (1, "text/plain", "f1") and len(d.parcels) == 2
    p3 = d.drop_text("summarizer", "摘要師", "s2", "## 重點\n- 三點開會")
    assert d.drop_text("summarizer", "摘要師", "s2", "again") is p3  # 同一步只包一次
    assert (p3.tag, p3.name, p3.size) == (3, "摘要師的成果.txt", len("## 重點\n- 三點開會".encode()))
    assert [p.tag for p in d.from_steps(["s1"])] == [1, 2]
    files = await d.pick_up(d.parcels)  # f2 找不到（過期）就略過
    assert [(f.name, f.data) for f in files] == [("會議_逐字稿.txt", b"transcript"), ("摘要師的成果.txt", "## 重點\n- 三點開會".encode())]
    assert slip([p1]).endswith("#1 會議_逐字稿.txt（來自聽打員）")


def test_with_typist_reporter():
    out = with_typist(Plan(steps=[], source="direct"), "typist", "轉成逐字稿，做成會議記錄 PDF", summarizer="summarizer", reporter="rm")
    assert [(s.agent, s.use) for s in out.steps] == [("typist", []), ("rm", ["s0"])]
    out = with_typist(Plan(steps=[], source="direct"), "typist", "整理重點", summarizer="summarizer", reporter="rm")
    assert out.steps[-1].agent == "summarizer"
    # 村長只排了聽打員（漏了後面的工作）：接上
    only = Plan(steps=[Step("s1", "typist", "轉逐字稿")], source="llm")
    out = with_typist(only, "typist", "轉成逐字稿再做成 PDF 報告", summarizer="summarizer", reporter="rm", names={"rm": "文件彙整師"})
    assert [(s.id, s.agent, s.use) for s in out.steps] == [("s1", "typist", []), ("s0", "rm", ["s1"])]
    assert out.notes == ["請求還要做成報告：接著交給文件彙整師"]
    out = with_typist(only, "typist", "轉成逐字稿再整理待辦", summarizer="summarizer", reporter="rm")
    assert out.steps[-1].agent == "summarizer"
    assert with_typist(only, "typist", "轉成逐字稿", summarizer="summarizer", reporter="rm") is only
    # 已經有摘要師、只漏了報告：接在摘要師後面
    two = Plan(steps=[Step("s1", "typist", "轉"), Step("s2", "summarizer", "整理", ["s1"])], source="llm")
    assert [(s.agent, s.use) for s in with_typist(two, "typist", "整理後做成 PDF", reporter="rm").steps][-1] == ("rm", ["s2"])


class Fake(LLMClient):
    plan: list[dict] = []

    def __init__(self, kind, base_url, api_key):
        pass

    async def chat(self, model, messages, **kw):
        system = kw.get("system") or ""
        if "聽打員的助手" in system:
            return ChatResult(text='{"language": "zh", "terms": ""}', model=model)
        if kw.get("json_schema") and "你是村長" in system:
            Fake.last_system = system
            return ChatResult(text=json.dumps({"steps": Fake.plan, "loop": None}), model=model)
        return ChatResult(text="## 重點\n- 三點開會", model=model)

    async def list_models(self):
        return [RemoteModel(id="m")]

    async def transcribe(self, model, data, filename, *, language=None, prompt=None):
        return Transcript(text="今天下午三點開會", model=model, segments=[Segment(0, 3, "今天下午三點開會")])


@pytest.fixture
async def village(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    agent = FakeAgent()
    monkeypatch.setattr(remote, "_client", lambda timeout=10: httpx.AsyncClient(transport=httpx.MockTransport(agent.handler)))
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'v.db'}", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    app = create_app(settings, client_factory=Fake, handoff_delay=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
            p = (await c.post("/api/llm/providers", json={"name": "openai", "kind": "openai_compat", "base_url": "https://api.openai.com/v1"})).json()
            m = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "m"})).json()
            w = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "whisper-1", "output": "audio"})).json()
            await c.put("/api/llm/tiers", json={"large": [m["id"]]})
            rules = (await c.get("/api/rules")).json()
            await c.put("/api/rules", json={**rules, "reviewer_enabled": False, "memory_auto": False})
            assert (await c.post("/api/residents", json={"id": "rm", "url": "http://localhost:8101"})).status_code == 201
            yield c, app, agent, w["id"]


def _file_parts(call):
    return [(p["file"]["name"], base64.b64decode(p["file"]["bytes"])) for p in call["params"]["message"]["parts"] if p["kind"] == "file"]


def _text(call):
    return next(p["text"] for p in call["params"]["message"]["parts"] if p["kind"] == "text")


async def test_text_result_handed_to_report_maker(village):
    c, app, agent, _ = village
    Fake.plan = [{"id": "s1", "agent": "summarizer", "instruction": "整理重點", "use": []},
                 {"id": "s2", "agent": "rm", "instruction": "做成 PDF 報告", "use": ["s1"]}]
    r = (await c.post("/api/chat", json={"message": "整理重點再做成 PDF"})).json()
    msgs = await _wait(c, r)
    assert msgs[-1]["status"] == "done", msgs[-1]
    [call] = agent.calls
    assert _file_parts(call) == [("摘要師的成果.txt", "## 重點\n- 三點開會".encode())]
    assert "中轉站有前面步驟交給你的檔案" in _text(call) and "#1 摘要師的成果.txt（來自摘要師" in _text(call)
    assert "參考資料（來自摘要師）" not in _text(call)  # 已經用檔案交付：文字訊息不重複全文（對方的要求有字數上限）
    trace = (await c.get(f"/api/runs/{r['run_id']}")).json()
    assert trace["steps"][1]["received"][0].startswith("#1 摘要師的成果.txt") and trace["steps"][0]["received"] == []
    assert [a["name"] for a in msgs[-1]["meta"]["attachments"]] == ["營運彙整.pdf"]
    assert "也拿得到前面步驟的成果" in Fake.last_system


async def test_report_maker_without_anything_still_asks_for_files(village):
    c, app, agent, _ = village
    Fake.plan = [{"id": "s1", "agent": "rm", "instruction": "做報告", "use": []}]
    r = (await c.post("/api/chat", json={"message": "做報告"})).json()
    msgs = await _wait(c, r)
    assert msgs[-1]["status"] == "error" and not agent.calls


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="沒有 ffmpeg")
async def test_transcript_file_handed_to_report_maker(village):
    c, app, agent, whisper = village
    async with app.state.ctx.sessionmaker() as s:
        from datetime import UTC, datetime

        from village.store.models import LlmModel

        (await s.get(LlmModel, whisper)).audio_verified_at = datetime.now(UTC)
        await s.commit()
    tiers = (await c.get("/api/llm/tiers")).json()
    await c.put("/api/llm/tiers", json={**tiers, "audio": [whisper]})
    up = (await c.post("/api/files/audio", params={"name": "週會.mp3"}, content=audio_test.AUDIO.read_bytes())).json()
    Fake.plan = []  # 村長放棄：程式排「聽打員 → 文件彙整師」
    r = (await c.post("/api/chat", json={"message": "轉成逐字稿，做成會議記錄 PDF", "audio_ids": [up["id"]]})).json()
    msgs = await _wait(c, r)
    answer = msgs[-1]
    assert answer["status"] == "done", answer
    assert answer["meta"]["agents"] == ["typist", "rm"]
    [call] = agent.calls
    [(name, data)] = _file_parts(call)  # srt 不是它收的格式，不給
    assert name == "週會_逐字稿.txt" and "今天下午三點開會" in data.decode()
    # 回答附上每一步的檔案：報告 PDF 在前，逐字稿與字幕也下載得到
    assert [a["name"] for a in answer["meta"]["attachments"]] == ["營運彙整.pdf", "週會_逐字稿.txt", "週會_字幕.srt"]
    trace = (await c.get(f"/api/runs/{r['run_id']}")).json()
    assert trace["plan"]["notes"][-1] == "請求還要做成報告：接著交給文件彙整師"
