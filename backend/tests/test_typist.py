"""聽打員：錄音上傳 → audio 分級的語音轉文字模型（切段、同時轉寫）→ 繁體逐字稿＋txt / srt 檔，錄音轉完就刪。"""

import asyncio
import json
import shutil
import subprocess
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from village.config import REPO_ROOT, Settings
from village.core import audio
from village.core.executor import with_typist
from village.core.orchestrator import Plan, Step
from village.llm import audio_test
from village.llm.base import ChatResult, LLMClient, LLMError, RemoteModel, Segment, Transcript
from village.llm.openai_compat import _QUIRKS, OpenAICompatClient
from village.main import create_app
from village.store.models import ChatFile, LlmModel

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="沒有 ffmpeg")
MP3 = audio_test.AUDIO.read_bytes()


# ---------------------------------------------------------------- 格式與排版


def test_audio_checks_and_layout():
    assert audio.check_head("a.mp3", MP3) == "audio/mpeg"
    assert audio.check_head("a.m4a", b"\x00\x00\x00\x20ftypM4A ") == "audio/mp4"
    assert audio.check_head("a.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ") == "audio/wav"
    with pytest.raises(audio.AudioError, match="不是支援的錄音格式"):
        audio.check_head("a.txt", b"hello")
    with pytest.raises(audio.AudioError, match="不是有效的 mp3"):
        audio.check_head("a.mp3", b"%PDF-1.7")

    assert audio.to_traditional("我们讨论软件和服务器") == "我們討論軟體和伺服器"
    assert audio.to_traditional("我們討論軟體") == "我們討論軟體" and audio.to_traditional("hello") == "hello"
    assert audio.to_traditional("会議は三時からです") == "会議は三時からです"  # 日文不轉
    assert audio.join(["Hello", "world.", "你好", "世界"]) == "Hello world.你好世界"
    assert audio.join(["（第一句）好。", "（第二句）"]) == "（第一句）好。（第二句）"

    segs = [Segment(i * 20, i * 20 + 20, f"第{i}句。") for i in range(7)]  # 每段 20 秒
    paras = audio.paragraphs(segs)
    assert [t for t, _ in paras] == [0, 60, 120] and paras[0][1] == "第0句。第1句。第2句。"
    assert audio.render_paragraphs(paras, hours=False).startswith("〔00:00〕第0句。")
    assert audio.render_paragraphs([(3725, "x")], hours=True) == "〔1:02:05〕x"
    srt = audio.to_srt([Segment(0, 1.5, "你好"), Segment(3661.2, 3661.2, "再見")])
    assert "1\n00:00:00,000 --> 00:00:01,500\n你好\n" in srt and "01:01:01,200 --> 01:01:01,700" in srt
    long = "一二三四五六七八九十。" * 60
    parts = audio.split_text(long, 600)
    assert parts[0][0] == 600 and all(t is None for t, _ in parts[1:]) and "".join(p for _, p in parts) == long
    assert audio.length_text(3725) == "1 小時 2 分" and audio.length_text(75) == "1 分 15 秒"


def test_audio_quiz_grading():
    assert audio_test.grade("今天下午3點，大家在村子的廣場開會。").passed
    assert audio_test.grade("今天下午三点大家在村子的广场开会").passed  # 簡體也算
    assert not audio_test.grade("Thank you for watching.").passed
    assert not audio_test.grade("").passed


@needs_ffmpeg
async def test_split_with_ffmpeg(tmp_path):
    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=25", str(src)], check=True)
    assert 24.5 < await audio.duration_of(src) < 25.5
    chunks = await audio.split(src, tmp_path, chunk_seconds=10)
    assert [round(c.start) for c in chunks] == [0, 10, 20] and round(chunks[-1].end) == 25
    assert all(c.path.read_bytes()[:3] in (b"ID3",) or c.path.read_bytes()[0] == 0xFF for c in chunks)
    with pytest.raises(audio.AudioError, match="讀不了"):
        bad = tmp_path / "bad.mp3"
        bad.write_bytes(b"ID3" + b"\x00" * 100)
        await audio.duration_of(bad)


# ---------------------------------------------------------------- OpenAI 相容的 /audio/transcriptions


async def test_openai_transcriptions_api():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request):
        seen.append(request)
        body = request.content.decode(errors="replace")
        if "verbose_json" in body and "gpt-4o" in body:
            return httpx.Response(400, json={"error": {"message": "response_format 'verbose_json' is not compatible", "param": "response_format"}})
        if "verbose_json" in body:
            return httpx.Response(200, json={"text": "你好 世界", "duration": 4.2, "segments": [
                {"start": 0, "end": 1.5, "text": " 你好"}, {"start": 1.5, "end": 4.2, "text": "世界 "}]})
        return httpx.Response(200, json={"text": "沒有時間戳"})

    client = OpenAICompatClient("https://api.openai.com/v1", "k")
    client._http = httpx.AsyncClient(base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(handler))
    r = await client.transcribe("whisper-1", MP3, "a.mp3", language="zh", prompt="以下是繁體中文")
    assert r.duration == 4.2 and [s.text for s in r.segments] == ["你好", "世界"]
    form = seen[-1].content.decode(errors="replace")
    assert seen[-1].url.path == "/v1/audio/transcriptions" and 'name="timestamp_granularities[]"' in form
    assert 'name="language"' in form and "audio/mpeg" in form

    # 不支援 verbose_json 的模型：400 → 改要 json 並記住（下次不用再失敗一次）
    _QUIRKS.pop(("https://api.openai.com/v1", "my-gpt-4o-model"), None)
    r = await client.transcribe("my-gpt-4o-model", MP3, "a.mp3")
    assert r.text == "沒有時間戳" and r.segments == [] and len(seen) == 3
    await client.transcribe("my-gpt-4o-model", MP3, "a.mp3")
    assert len(seen) == 4
    # gpt-4o-transcribe 一開始就要 json
    await client.transcribe("gpt-4o-transcribe", MP3, "a.mp3")
    assert "verbose_json" not in seen[-1].content.decode(errors="replace")
    await client.aclose()


# ---------------------------------------------------------------- 村子


class Fake(LLMClient):
    """村長：plan_with 決定計畫；聽打員助手回語言；語音模型回簡體字（測試轉繁體）。"""

    plan_with: list[dict] = []
    transcribed: list[tuple[str, str | None]] = []

    def __init__(self, kind, base_url, api_key):
        pass

    async def chat(self, model, messages, **kw):
        system = kw.get("system") or ""
        if "聽打員的助手" in system:
            return ChatResult(text='{"language": "zh", "terms": "村長"}', model=model)
        if kw.get("json_schema") and "你是村長" in system:
            return ChatResult(text=json.dumps({"steps": Fake.plan_with, "loop": None}), model=model)
        if messages and "逐字稿" in messages[-1].content:
            return ChatResult(text="重點：三點開會", model=model)
        return ChatResult(text="村長回答", model=model)

    async def list_models(self):
        return [RemoteModel(id="gpt-5"), RemoteModel(id="whisper-1"), RemoteModel(id="gpt-4o-mini-transcribe")]

    async def list_audio_models(self):
        return [m for m in await self.list_models() if "whisper" in m.id or "transcribe" in m.id]

    async def transcribe(self, model, data, filename, *, language=None, prompt=None):
        if "broken" in model:
            raise LLMError("HTTP 404: not found")
        Fake.transcribed.append((filename, prompt))
        if model == "quiz":
            return Transcript(text=audio_test.EXPECTED, model=model)
        return Transcript(text="今天下午三点开会", model=model, segments=[Segment(0, 2, "今天下午三点"), Segment(2, 4.5, "开会")])


@pytest.fixture
async def village(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    Fake.plan_with, Fake.transcribed = [], []
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'v.db'}", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    app = create_app(settings, client_factory=Fake, handoff_delay=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
            p = (await c.post("/api/llm/providers", json={"name": "openai", "kind": "openai_compat", "base_url": "https://api.openai.com/v1"})).json()
            ids = {"chat": (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": "gpt-5"})).json()["id"]}
            for name in ("whisper-1", "quiz", "broken-audio"):
                ids[name] = (await c.post("/api/llm/models", json={"provider_id": p["id"], "model": name, "output": "audio"})).json()["id"]
            await c.put("/api/llm/tiers", json={"large": [ids["chat"]]})
            rules = (await c.get("/api/rules")).json()
            await c.put("/api/rules", json={**rules, "reviewer_enabled": False, "memory_auto": False})
            yield c, app, p, ids


async def _wait(c, r, n=2):
    for _ in range(300):
        msgs = (await c.get(f"/api/conversations/{r['conversation_id']}/messages")).json()
        if len(msgs) >= n and msgs[-1]["role"] == "assistant":
            return msgs
        await asyncio.sleep(0.02)
    raise AssertionError("no answer")


async def _upload(c, name="會議.mp3", data=MP3):
    return await c.post("/api/files/audio", params={"name": name}, content=data)


async def test_audio_models_quiz_and_tiers(village):
    c, app, p, ids = village
    r = (await c.get(f"/api/llm/providers/{p['id']}/remote-models?output=audio")).json()
    assert [m["id"] for m in r] == ["gpt-4o-mini-transcribe", "whisper-1"]

    r = await c.put("/api/llm/tiers", json={"audio": [ids["quiz"]]})
    assert r.status_code == 422 and "轉寫測驗" in r.json()["detail"]
    r = (await c.post(f"/api/llm/models/{ids['quiz']}/audio-test")).json()
    assert r["passed"] and r["model"]["audio_verified_at"] and r["score"] == 1.0
    assert Fake.transcribed[-1] == ("audio_test.mp3", "以下是繁體中文的逐字稿。")
    r = (await c.post(f"/api/llm/models/{ids['broken-audio']}/audio-test")).json()
    assert not r["passed"] and "404" in r["answer"]
    assert (await c.post(f"/api/llm/models/{ids['chat']}/audio-test")).status_code == 422  # 不是語音模型

    assert (await c.put("/api/llm/tiers", json={"large": [ids["chat"]], "audio": [ids["quiz"]]})).status_code == 200
    r = await c.put("/api/llm/tiers", json={"large": [ids["quiz"]]})
    assert r.status_code == 422 and "只能放進 image / audio" in r.json()["detail"]
    r = await c.put("/api/llm/tiers", json={"audio": [ids["chat"]]})
    assert r.status_code == 422 and "只能放語音轉文字模型" in r.json()["detail"]

    from village.llm.service import llm_user

    me = (await c.get("/api/auth/status")).json()["user"]["id"]
    with llm_user(me):
        with pytest.raises(LLMError, match="不能拿來對話"):
            await app.state.ctx.llm.chat(ids["quiz"], [])


async def test_upload_needs_audio_model(village):
    c, app, _, ids = village
    r = await c.get("/api/files/audio/ready")
    assert r.json()["ok"] is False and "語音轉文字模型" in r.json()["reason"]
    r = await _upload(c)
    assert r.status_code == 409 and "audio 分級" in r.json()["detail"]

    await c.post(f"/api/llm/models/{ids['whisper-1']}/audio-test")  # Fake 回的字不對：不會通過
    await c.post(f"/api/llm/models/{ids['quiz']}/audio-test")
    await c.put("/api/llm/tiers", json={"large": [ids["chat"]], "audio": [ids["quiz"]]})
    assert (await c.get("/api/files/audio/ready")).json()["ok"] is True
    r = await _upload(c, "a.txt", b"hello")
    assert r.status_code == 422 and "不是支援的錄音格式" in r.json()["detail"]
    r = await _upload(c, "a.mp3", b"%PDF-1.7 not audio")
    assert r.status_code == 422 and "不是有效的 mp3" in r.json()["detail"]
    r = await c.post("/api/chat", json={"message": "轉逐字稿", "audio_ids": ["nope"]})
    assert r.status_code == 422 and "重新附上" in r.json()["detail"]


async def _ready(c, app, ids, model="whisper-1"):
    """讓 whisper-1 當 audio 分級（直接記上測驗時間：Fake 的 whisper-1 回答和測驗內容不同）。"""
    async with app.state.ctx.sessionmaker() as s:
        m = await s.get(LlmModel, ids[model])
        m.audio_verified_at = datetime.now(UTC)
        await s.commit()
    await c.put("/api/llm/tiers", json={"large": [ids["chat"]], "audio": [ids[model]]})


@needs_ffmpeg
async def test_transcribe_flow(village):
    c, app, _, ids = village
    await _ready(c, app, ids)
    up = (await _upload(c)).json()
    assert up["name"] == "會議.mp3" and up["size"] == len(MP3)

    # 村長沒派聽打員（弱模型）：程式補上第一步
    r = (await c.post("/api/chat", json={"message": "", "audio_ids": [up["id"]]})).json()
    msgs = await _wait(c, r)
    assert msgs[0]["content"] == "請把這段錄音轉成逐字稿"
    assert msgs[0]["meta"]["files"] == [{"id": up["id"], "name": "會議.mp3", "size": len(MP3), "audio": True}]
    answer = msgs[-1]
    assert answer["status"] == "done", answer
    assert "〔00:00〕今天下午三點開會" in answer["content"] and "錄音已刪除" in answer["content"]
    assert answer["meta"]["agents"] == ["typist"]
    files = {a["name"]: a for a in answer["meta"]["attachments"]}
    assert set(files) == {"會議_逐字稿.txt", "會議_字幕.srt"}
    txt = await c.get(files["會議_逐字稿.txt"]["url"])
    assert "今天下午三點開會" in txt.text and txt.headers["content-type"].startswith("text/plain")
    srt = (await c.get(files["會議_字幕.srt"]["url"])).text
    assert "00:00:02,000 --> 00:00:04,500\n開會" in srt
    assert Fake.transcribed[-1] == ("000.mp3", "以下是繁體中文的逐字稿。可能出現的名詞：村長。")

    async with app.state.ctx.sessionmaker() as s:  # 錄音轉完就刪掉
        assert (await s.scalars(select(ChatFile).where(ChatFile.kind == "audio"))).all() == []
    r = await c.post("/api/chat", json={"message": "再轉一次", "audio_ids": [up["id"]]})
    assert r.status_code == 422  # 用過的錄音不能再用


@needs_ffmpeg
async def test_typist_then_summary(village):
    c, app, _, ids = village
    await _ready(c, app, ids)
    up = (await _upload(c)).json()
    # 村長只排了「整理重點」：聽打員補在前面，整理重點的步驟改成用逐字稿
    Fake.plan_with = [{"id": "s1", "agent": "poet", "instruction": "整理會議重點", "use": []}]
    r = (await c.post("/api/chat", json={"message": "幫我整理這段會議的重點", "audio_ids": [up["id"]]})).json()
    msgs = await _wait(c, r)
    assert msgs[-1]["meta"]["agents"] == ["typist", "poet"] and msgs[-1]["content"] == "重點：三點開會"
    trace = (await c.get(f"/api/runs/{r['run_id']}")).json()
    assert [s["use"] for s in trace["steps"]] == [[], ["s0"]]
    assert "附了錄音：先交給聽打員轉成逐字稿" in trace["plan"]["notes"]


async def test_too_long_recording(village):
    c, app, _, ids = village
    await _ready(c, app, ids)
    rules = (await c.get("/api/rules")).json()
    await c.put("/api/rules", json={**rules, "transcribe_max_minutes": 0})
    assert (await _upload(c)).status_code == 409


def test_with_typist():
    plan = Plan(steps=[Step("s1", "poet", "寫重點"), Step("s2", "translator", "翻成英文", ["s1"])], source="llm")
    out = with_typist(plan, "typist", "msg")
    assert [(s.id, s.agent, s.use) for s in out.steps] == [("s0", "typist", []), ("s1", "poet", ["s0"]), ("s2", "translator", ["s1"])]
    out = with_typist(Plan(steps=[], source="direct"), "typist", "轉成文字")
    assert out.steps[0].instruction == "轉成文字" and out.source == "llm"
