import json

import httpx
import pytest

from village.core import workshop
from village.core.workshop import Neighbor, SpriteOption, build_spec, pick_home, slugify, unique_id
from village.llm.base import ChatMessage, ChatResult, LLMError
from test_villagers import env  # noqa: F401  （共用 app fixture）

SPRITES = [SpriteOption("chef", "廚師", "料理"), SpriteOption("scholar", "學者", "知識")]
NEIGHBORS = [Neighbor("poet", "詩人", "寫短詩", (7, 6)), Neighbor("jester", "笑話師", "說笑話", (22, 6))]

BLUEPRINT = {
    "name": "食譜師",
    "id": "Recipe Maker!",
    "description": "提供家常菜食譜。適合「怎麼煮、食譜、這道菜要什麼材料」等需求。",
    "skill": "依菜名給出材料與步驟",
    "examples": ["番茄炒蛋怎麼做", "給我一個咖哩食譜"],
    "keywords": ["食譜", "怎麼煮", "料理"],
    "tags": ["cooking"],
    "sprite": "chef",
    "creativity": "stable",
    "max_chars": 1000,
    "system_prompt": "你是村子裡的食譜師，親切實在。\n規則：\n- 列出材料與步驟。\n- 只輸出食譜本身。",
    "tests": [
        {"input": "番茄炒蛋怎麼做", "context": "", "contains": ["蛋", "番茄", "美味"], "not_contains": ["以下是", "好的"], "lang": "zh"},
        {"input": "", "context": "", "contains": [], "not_contains": [], "lang": "any"},  # 空的會被略過
    ],
    "summary": "做好了一位食譜師！",
}


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[ChatMessage]] = []

    async def chat(self, target, messages, **kw):
        self.calls.append(list(messages))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return ChatResult(text=r if isinstance(r, str) else json.dumps(r, ensure_ascii=False), model="fake")


def test_helpers():
    assert slugify("Recipe Maker!") == "recipe-maker"
    assert slugify("123 食譜") == "villager"
    assert unique_id("poet", {"poet", "poet-2"}) == "poet-3"
    assert unique_id("mayor", set()) == "mayor-villager"
    assert unique_id("craftsman", set()) == "craftsman-villager"
    h = pick_home([(7, 6), (22, 6)])
    assert h not in [(7, 6), (22, 6), (15, 9), (26, 16)] and h[1] not in (9, 10) and h[0] != 15


def test_build_spec_maps_choices():
    spec = build_spec(BLUEPRINT, SPRITES, NEIGHBORS)
    assert spec.id == "recipe-maker" and spec.village.sprite == "chef"
    assert spec.model.temperature == 0.2 and spec.checks.max_chars == 1000 and spec.model.max_tokens == 3072
    assert spec.prompt.system.endswith("- 長度：全部內容不超過 1000 字。寧可精簡；寫不下時減少點數或縮短每一點，不要硬塞。")
    assert spec.prompt.user_template == workshop.DEFAULT_TEMPLATE
    assert len(spec.tests) == 1 and spec.tests[0].expect.lang == "zh" and spec.tests[0].expect.max_chars == 1000
    assert spec.tests[0].expect.not_contains == ["以下是"]  # 太短的詞拿掉
    assert spec.tests[0].expect.contains == ["蛋", "番茄"]  # 任務裡沒有的詞（美味）是猜的，拿掉
    bad = {**BLUEPRINT, "sprite": "dragon", "creativity": "wild"}
    spec = build_spec(bad, SPRITES, NEIGHBORS)
    assert spec.village.sprite == "chef" and spec.model.temperature == 0.6  # 不在清單的值改用預設
    with pytest.raises(workshop.BlueprintError, match="重複"):
        build_spec({**BLUEPRINT, "name": "詩人"}, SPRITES, NEIGHBORS)
    with pytest.raises(workshop.BlueprintError, match="keywords"):
        build_spec({**BLUEPRINT, "keywords": ["食譜"]}, SPRITES, NEIGHBORS)


def test_build_spec_keeps_draft_identity():
    draft = build_spec(BLUEPRINT, SPRITES, NEIGHBORS).to_doc()
    draft["x-village"]["home"] = [3, 3]
    draft["model"]["model_id"] = "m1"
    draft["card"]["skills"][0]["id"] = "cook"
    draft["tests"].append({"input": "使用者自己寫的", "context": "", "expect": {"contains": ["x"], "max_chars": None}})
    # 工匠回傳的守則已含上一輪的長度規則 → 換成新的，不會重複
    bp = {**BLUEPRINT, "id": "other", "creativity": "creative", "max_chars": 300, "system_prompt": draft["prompt"]["system"]}
    spec = build_spec(bp, SPRITES, NEIGHBORS, draft)
    assert spec.id == "recipe-maker" and spec.village.home == (3, 3) and spec.card.skills[0].id == "cook"
    assert spec.model.model_id == "m1" and spec.model.temperature == 0.9
    assert spec.prompt.system.count("- 長度：") == 1 and "不超過 300 字" in spec.prompt.system
    assert [t.input for t in spec.tests] == ["番茄炒蛋怎麼做", "使用者自己寫的"]  # 保留原本的測試題
    assert spec.tests[0].expect.max_chars == 300 and spec.tests[1].expect.max_chars is None
    assert build_spec({**BLUEPRINT, "max_chars": 99999}, SPRITES, NEIGHBORS).checks.max_chars == 5000


async def test_generate_retries_with_feedback_then_falls_back():
    llm = FakeLLM("不是 JSON", BLUEPRINT)
    d = await workshop.generate(llm, [ChatMessage("user", "我要一個會教煮菜的人")], SPRITES, NEIGHBORS)
    assert d.source == "llm" and d.spec["card"]["name"] == "食譜師" and d.summary == "做好了一位食譜師！"
    assert "藍圖有問題" in llm.calls[1][-1].content

    llm = FakeLLM(LLMError("down"))
    msgs = [ChatMessage("user", "我要一個會教煮菜的人"), ChatMessage("assistant", "?"), ChatMessage("user", "要簡單")]
    d = await workshop.generate(llm, msgs, SPRITES, NEIGHBORS)
    assert d.source == "fallback" and d.spec["x-village"]["keywords"] == []
    assert "我要一個會教煮菜的人" in d.spec["card"]["description"] and "- 要簡單" in d.spec["prompt"]["system"]


async def test_interview_paths():
    llm = FakeLLM({"reply": "你平常會怎麼問他？", "ready": False})
    r = await workshop.interview(llm, [ChatMessage("user", "做一個廚師")], NEIGHBORS)
    assert (r.reply, r.ready, r.source) == ("你平常會怎麼問他？", False, "llm")

    r = await workshop.interview(FakeLLM(LLMError("x")), [ChatMessage("user", "做一個廚師")], NEIGHBORS)
    assert r.source == "fallback" and r.reply == workshop.FALLBACK_QUESTIONS[0] and not r.ready

    many = [ChatMessage("user", "a"), ChatMessage("assistant", "b")] * 5
    r = await workshop.interview(FakeLLM(), many, NEIGHBORS)
    assert r.ready and r.source == "rule"


async def test_workshop_api(env, monkeypatch):  # noqa: F811
    c, _, _ = env
    seen = []

    async def fake_generate(llm, messages, sprites, neighbors, **kw):
        seen.append(([n.id for n in neighbors], [s.id for s in sprites]))
        return workshop.Draft(spec=build_spec(BLUEPRINT, sprites, neighbors).to_doc(), notes=["ok"])

    monkeypatch.setattr(workshop, "generate", fake_generate)
    body = {"messages": [{"role": "user", "content": "做一個食譜師"}], "sprites": [{"id": "chef", "name": "廚師"}]}
    r = await c.post("/api/workshop/generate", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["card"]["id"] == "recipe-maker"
    assert set(seen[0][0]) >= {"poet", "jester"} and seen[0][1] == ["chef"]
    # 產生的草稿可以直接建立
    assert (await c.post("/api/villagers", json={"spec": r.json()["spec"]})).status_code == 201
    # 工匠是保留名稱
    spec = r.json()["spec"]
    spec["card"]["id"] = "craftsman"
    assert (await c.post("/api/villagers", json={"spec": spec})).status_code == 422
