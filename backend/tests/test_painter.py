"""Phase 6G-B：畫家。提示詞整理 → 生圖 → 存成畫作；張數上限；審核員退件不重畫；LLM 不能用時用原話畫。"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from test_phase1 import FakeLLM, Recorder, registry  # noqa: F401
from village.core.executor import Runner, StepResult, run_agent
from village.core.painter import PaintEnv, painting
from village.llm.base import GeneratedImage, ImageResult, LLMError
from village.tools import TOOLS

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ARGS = {"prompt": "a cat sleeping under the moon, watercolor", "aspect_ratio": "16:9", "title": "月下貓", "use_reference": False}


class FakeImages:
    """假的 LLMService.generate_image：記錄提示詞。"""

    def __init__(self, data=PNG, fail=False, no_refs=False):
        self.prompts: list[tuple[str, str | None]] = []
        self.refs: list = []
        self.data, self.fail, self.no_refs = data, fail, no_refs

    async def generate_image(self, target, prompt, *, aspect_ratio=None, references=None):
        assert target == "tier:image"
        self.prompts.append((prompt, aspect_ratio))
        self.refs.append(references)
        if self.fail:
            raise LLMError("你還沒有設定生圖模型")
        if references and self.no_refs:
            raise LLMError("img-model: HTTP 400: input_references not supported")
        return ImageResult(images=[GeneratedImage("image/png", self.data)], model="img-model", cost=0.04)


class FakeStore:
    def __init__(self, recent=0):
        self.saved: list = []
        self.recent = recent

    async def count_generated_since(self, user_id, since):
        assert since < datetime.now(UTC) - timedelta(hours=23)
        return self.recent + len(self.saved)

    async def save_generated(self, user_id, conversation_id, images):
        self.saved += images
        return [f"img{len(self.saved)}"]


def env(images=None, store=None, per_run=1, per_day=20, refs=None):
    return PaintEnv(images or FakeImages(), store or FakeStore(), "u1", "c1", per_run, per_day, references=refs or [])


async def test_painter_tool_paints_and_returns_url():
    e = env()
    with painting(e):
        res = await TOOLS["painter"].arun(ARGS)
    assert e.llm.prompts == [(ARGS["prompt"], "16:9")]
    [att] = res.attachments
    assert att.kind == "image" and att.url == "/api/images/img1" and att.title == "月下貓" and att.data == ""
    assert "月下貓" in res.text and ARGS["prompt"] in res.text and "$0.040" in res.detail


async def test_painter_limits_and_errors():
    p = TOOLS["painter"]
    with pytest.raises(LLMError, match="只能在問答"):
        await p.arun(ARGS)  # 沒有作畫環境
    e = env()
    with painting(e):
        await p.arun(ARGS)
        with pytest.raises(LLMError, match="每次 1 張"):
            await p.arun(ARGS)
    with painting(env(store=FakeStore(recent=20))), pytest.raises(LLMError, match="每人每天 20 張"):
        await p.arun(ARGS)
    with painting(env(per_run=0)), pytest.raises(LLMError, match="不開放畫圖"):
        await p.arun(ARGS)
    with painting(env(images=FakeImages(data=b"<svg/>"))), pytest.raises(LLMError, match="SVG"):
        await p.arun(ARGS)
    with painting(env(images=FakeImages(fail=True))), pytest.raises(LLMError, match="生圖模型"):
        await p.arun(ARGS)
    from village.tools.base import ToolError

    with pytest.raises(ToolError):
        await p.arun({"prompt": " ", "aspect_ratio": "1:1", "title": "x"})


async def test_painter_villager_falls_back_to_plain_words(registry):  # noqa: F811
    def down(t, m, kw):
        raise LLMError("no llm")

    spec = registry.get("painter")
    r = StepResult(id="s1", agent="painter", instruction="畫一隻貓", use=[])
    e = env()
    with painting(e):
        await run_agent(FakeLLM(down), spec, "畫一隻貓", "", r)
    assert e.llm.prompts[0][0] == "畫一隻貓" and r.attachments[0]["url"] == "/api/images/img1"
    assert any("程式直接" in w for w in r.warnings)


async def test_reviewer_rejection_does_not_repaint(registry):  # noqa: F811
    from village.store.settings import VillageRules

    plan = {"steps": [{"id": "s1", "agent": "painter", "instruction": "畫一隻貓", "use": []}]}

    def handler(target, messages, kw):
        schema = json.dumps(kw.get("json_schema") or {})
        if '"pass"' in schema:
            return json.dumps({"pass": False, "reason": "不像貓", "fix": "重畫", "step": "s1"})
        if '"aspect_ratio"' in schema:
            return json.dumps(ARGS)
        return json.dumps(plan)

    class Rules:
        current = VillageRules(reviewer_enabled=True, review_redo=2, parallel=False)

    e, bus = env(per_run=4), Recorder()
    out = await Runner(registry, FakeLLM(handler), bus, handoff_delay=0, rules=Rules()).run("r", "畫一隻貓", paint=e)
    assert len(e.llm.prompts) == 1  # 退件但沒有重畫
    assert out["attachments"][0]["url"] == "/api/images/img1"
    assert "agent.retry" not in bus.types() and bus.types().count("review.finished") == 1


def test_prune_keeps_requested_paintings_and_drops_extra_ones(registry):  # noqa: F811
    from village.core.orchestrator import Step, planner_system_prompt, prune_unrequested

    agents = {a.id: a for a in registry.all()}
    # 第八步：兩張都是原話 → 保留；「合併成一幅」沒要求 → 移除
    steps = [Step("s1", "painter", "畫一隻狗"), Step("s2", "painter", "畫一隻兔子"),
             Step("s3", "painter", "將剛剛畫的狗和兔子合併成一幅畫", ["s1", "s2"])]
    notes: list[str] = []
    kept = prune_unrequested(steps, "畫一隻狗，再畫一隻兔子", agents, notes)
    assert [s.id for s in kept] == ["s1", "s2"] and "s3" in notes[0]
    # 第九步：把鑑定結果又畫一次 → 移除重複的畫家步驟
    steps = [Step("s1", "painter", "把這張自然景觀的場景畫成像素風插畫"),
             Step("s2", "painter", "畫出一條流動的河流，兩側有茂密的樹木，背景是一座高大的山峰", ["s1"])]
    kept = prune_unrequested(steps, "把這張照片的場景畫成像素風插畫", agents, [])
    assert [s.id for s in kept] == ["s1"]
    # 詩人 + 畫家：照常保留
    steps = [Step("s1", "poet", "寫一首關於海的短詩"), Step("s2", "painter", "幫這首詩配一張插圖", ["s1"])]
    assert len(prune_unrequested(steps, "寫一首關於海的短詩，再幫它配一張插圖", agents, [])) == 2
    roster = planner_system_prompt(registry.all())
    assert "每次都要花錢" in roster and "上一張畫作為底修改" in roster


# ---------------------------------------------------------------- 修圖


def _png(w, h):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"


def _jpeg(w, h):
    sof = b"\xff\xc0\x00\x11\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + b"\xff\xe0\x00\x04\x00\x00" + sof + b"\xff\xd9"


def test_dimensions():
    from village.core.imagefmt import dimensions

    assert dimensions(_png(800, 600)) == (800, 600)
    assert dimensions(_jpeg(1035, 1300)) == (1035, 1300)
    assert dimensions(b"GIF89a") is None


async def test_edit_uses_reference_and_tells_the_painter():
    from village.core.painter import Reference

    ref = Reference("image/jpeg", _jpeg(1300, 975), "upload")
    e = env(refs=[ref])
    p = TOOLS["painter"]
    with painting(e):
        assert "使用者附的圖（1300×975，橫的）" in p.context()
        res = await p.arun({**ARGS, "prompt": "Turn this photo into pixel art", "use_reference": True})
    [refs] = e.llm.refs
    assert len(refs) == 1 and refs[0].mime == "image/jpeg"
    assert "依參考圖改好了" in res.text
    # LLM 說 false（只參考內容另外畫）：不送參考圖
    e = env(refs=[ref])
    with painting(e):
        res = await p.arun(ARGS)
    assert e.llm.refs == [None] and "畫好了" in res.text
    with painting(env()):
        assert "沒有參考圖" in p.context()


async def test_model_without_reference_support_repaints_from_text():
    from village.core.painter import Reference

    e = env(images=FakeImages(no_refs=True), refs=[Reference("image/png", _png(10, 10), "painting")])
    with painting(e):
        assert "要修改上一張畫作" in TOOLS["painter"].context()
        res = await TOOLS["painter"].arun({**ARGS, "use_reference": True})
    assert e.llm.refs[0] and e.llm.refs[1] is None  # 先帶參考圖失敗（400），再只依描述畫
    assert "不收參考圖" in res.text and e.used == 1 and len(e.store.saved) == 1


async def test_fallback_uses_reference_when_present(registry):  # noqa: F811
    from village.core.painter import Reference

    def down(t, m, kw):
        raise LLMError("no llm")

    e = env(refs=[Reference("image/png", _png(10, 10), "upload")])
    r = StepResult(id="s1", agent="painter", instruction="改成像素風", use=[])
    with painting(e):
        await run_agent(FakeLLM(down), registry.get("painter"), "改成像素風", "", r)
    assert e.llm.refs[0] and r.tool_args["use_reference"] is True
