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
ARGS = {"prompt": "a cat sleeping under the moon, watercolor", "aspect_ratio": "16:9", "title": "月下貓"}


class FakeImages:
    """假的 LLMService.generate_image：記錄提示詞。"""

    def __init__(self, data=PNG, fail=False):
        self.prompts: list[tuple[str, str | None]] = []
        self.data, self.fail = data, fail

    async def generate_image(self, target, prompt, *, aspect_ratio=None):
        assert target == "tier:image"
        self.prompts.append((prompt, aspect_ratio))
        if self.fail:
            raise LLMError("你還沒有設定生圖模型")
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


def env(images=None, store=None, per_run=1, per_day=20):
    return PaintEnv(images or FakeImages(), store or FakeStore(), "u1", "c1", per_run, per_day)


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
