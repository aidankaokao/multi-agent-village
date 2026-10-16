"""工匠（村民工坊）：透過對話了解使用者想要的村民，產生一份村民設定草稿。

和村長一樣是系統角色，不會被派去做問答任務。分成兩個小任務，弱模型也比較穩：
  1. interview —— 判斷資訊夠不夠；不夠就用日常的問題追問（輸出 {reply, ready}）
  2. generate  —— 產生「藍圖」（扁平、好填的欄位），再由程式組成完整 AgentSpec

交給程式、不交給 LLM 的部分：ID 轉換與避免重複、外觀只能從現有角色選、住處挑地圖空位、
任務模板固定、temperature / 字數上限由「穩定 / 創意」「長短」這種選項換算。
LLM 不能用時有退路：追問用固定的問題，生成用對話內容拼出基本草稿。
"""

import json
import logging
import math
import re
from dataclasses import dataclass, field

from village.core.card import AgentSpec
from village.core.orchestrator import ChatLLM, PlanError, _extract_json
from village.core.registry import RESERVED_IDS
from village.llm.base import ChatMessage, LLMError

log = logging.getLogger("village.workshop")

CRAFTSMAN_ID = "craftsman"
GEN_RETRIES = 2
MAX_INTERVIEW_ROUNDS = 4  # 使用者回答幾輪後就不再追問，直接請他生成

# 地圖（與前端 VillageScene 相同）：住處避開村長、工匠與道路
MAP_W, MAP_H = 30, 20
SYSTEM_HOMES = [(15, 9), (26, 16)]  # 村長、工匠
ROAD_ROW, ROAD_COL = 9, 15

CREATIVITY = {"stable": 0.2, "balanced": 0.6, "creative": 0.9}
LENGTH = {  # 字數上限, max_tokens
    "short": (300, 1024),
    "medium": (1000, 2048),
    "long": (3000, 4096),
}
DEFAULT_TEMPLATE = "任務：{instruction}\n{context}"


@dataclass
class SpriteOption:
    id: str
    name: str
    desc: str = ""


@dataclass
class Neighbor:
    """已經在村子裡的村民：避免重複 id / 住處，也讓工匠知道分工。"""

    id: str
    name: str
    description: str
    home: tuple[int, int] | None = None


@dataclass
class Draft:
    spec: dict
    notes: list[str] = field(default_factory=list)  # 過程紀錄（重做、改用退路）
    summary: str = ""  # 工匠對使用者說的話
    source: str = "llm"  # llm / fallback
    model: str | None = None


# ---------------------------------------------------------------- 1. 追問

INTERVIEW_SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}, "ready": {"type": "boolean"}},
    "required": ["reply", "ready"],
    "additionalProperties": False,
}


def _roster(neighbors: list[Neighbor]) -> str:
    return "\n".join(f"- {n.name}：{n.description}" for n in neighbors) or "（目前沒有村民）"


def interview_system_prompt(neighbors: list[Neighbor], has_draft: bool) -> str:
    stage = (
        "使用者已經有一份草稿，現在是在說想怎麼修改。聽懂了就 ready=true，reply 用一句話複述要改什麼。"
        if has_draft
        else "你要弄清楚使用者想要什麼樣的新村民。"
    )
    return f"""你是村子裡的工匠，專門幫村民「打造」新夥伴。每位村民是一個 AI 小幫手，只做一件專門的事。
{stage}

村裡現有的村民：
{_roster(neighbors)}

要做出好村民，你需要知道（不用每項都問，能從對話推測的就不要問）：
A. 他負責什麼工作、主要服務什麼情境
B. 使用者平常會怎麼開口請他幫忙（實際會打的句子）
C. 他交出來的東西長什麼樣子、大概多長
D. 個性或語氣；每次回答要穩定一致，還是要有變化
E. 有沒有絕對不能做的事、或使用者最討厭的回答方式
F. 會不會接在其他村民後面工作（例如處理詩人寫的詩）

說話規則：
1. 用繁體中文，像朋友聊天一樣親切、簡短。
2. 一次最多問 2 個問題，用日常生活的說法問，可以舉例讓使用者好回答。
3. 絕對不要出現技術用語：prompt、temperature、token、關鍵字、JSON、欄位、設定、參數、模型。
4. 不要告訴使用者「缺少哪個欄位」，而是直接問問題。
5. 已經足夠打造（至少知道 A，並對 B 或 C 有概念），或使用者表示「就這樣 / 直接做」時，ready=true，
   reply 用 2～3 句話複述你理解的村民，最後請他按「打造村民」。
6. 資訊還不夠時 ready=false，reply 是你的問題。

只輸出 JSON：{{"reply": "...", "ready": true 或 false}}"""


# LLM 不能用時依序問的固定問題（同樣是日常說法）
FALLBACK_QUESTIONS = [
    "你平常會怎麼開口請他幫忙？可以直接寫兩三句你會打的句子。",
    "他交給你的東西大概長什麼樣子？一兩句話、一段文字，還是一整篇？可以舉個你理想中的例子。",
    "他的個性想要怎樣？每次回答都穩穩的差不多，還是每次都有點新花樣？有沒有什麼是他絕對不能做的？",
]


@dataclass
class InterviewReply:
    reply: str
    ready: bool
    source: str = "llm"


async def interview(
    llm: ChatLLM, messages: list[ChatMessage], neighbors: list[Neighbor], *, has_draft: bool = False,
    target: str = "tier:large",
) -> InterviewReply:
    user_turns = sum(1 for m in messages if m.role == "user")
    if user_turns > MAX_INTERVIEW_ROUNDS:
        return InterviewReply("我已經有足夠的想法了！按「打造村民」，我先做一版給你看，不滿意再跟我說要改哪裡。", True, "rule")
    try:
        r = await llm.chat(
            target, messages, system=interview_system_prompt(neighbors, has_draft),
            json_schema=INTERVIEW_SCHEMA, max_tokens=1024,
        )
        data = _extract_json(r.text)
        reply = str(data.get("reply") or "").strip()
        if reply:
            return InterviewReply(reply, bool(data.get("ready")))
    except (LLMError, PlanError) as e:
        log.warning("工匠追問失敗，改用固定問題：%s", e)
    if has_draft:
        return InterviewReply("收到！按「打造村民」，我照你說的改一版。", True, "fallback")
    i = user_turns - 1
    if 0 <= i < len(FALLBACK_QUESTIONS):
        return InterviewReply(FALLBACK_QUESTIONS[i], False, "fallback")
    return InterviewReply("了解！按「打造村民」，我先做一版給你看。", True, "fallback")


# ---------------------------------------------------------------- 2. 生成藍圖


def blueprint_schema(sprite_ids: list[str]) -> dict:
    s = {"type": "string"}
    strs = {"type": "array", "items": s}
    test = {
        "type": "object",
        "properties": {
            "input": s,
            "context": s,
            "contains": strs,
            "not_contains": strs,
            "lang": {"type": "string", "enum": ["zh", "en", "any"]},
        },
        "required": ["input", "context", "contains", "not_contains", "lang"],
        "additionalProperties": False,
    }
    props = {
        "name": s,
        "id": s,
        "description": s,
        "skill": s,
        "examples": strs,
        "keywords": strs,
        "tags": strs,
        "sprite": {"type": "string", "enum": sprite_ids} if sprite_ids else s,
        "creativity": {"type": "string", "enum": list(CREATIVITY)},
        "length": {"type": "string", "enum": list(LENGTH)},
        "system_prompt": s,
        "tests": {"type": "array", "items": test},
        "summary": s,
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def generate_system_prompt(sprites: list[SpriteOption], neighbors: list[Neighbor], draft: dict | None) -> str:
    looks = "\n".join(f"- {s.id}：{s.name}（{s.desc}）" for s in sprites) or "（無）"
    base = (
        f"\n目前的草稿（請在這個基礎上依對話修改，沒提到的部分保持原樣）：\n{json.dumps(_draft_digest(draft), ensure_ascii=False)}\n"
        if draft
        else ""
    )
    return f"""你是村子裡的工匠。根據對話，替使用者打造一位新村民，輸出他的「藍圖」JSON。
村民是一個 AI 小幫手：村長會依「名片」決定要不要派他工作，派工時給他一句任務（有時附上前一位村民的成果），他用自己的 system_prompt 完成。

村裡現有的村民（不要和他們重複；名字也不能一樣）：
{_roster(neighbors)}

可選的外觀：
{looks}
{base}
各欄位怎麼寫：
- name：2～4 個字的中文職稱，例如「翻譯官」「食譜師」。
- id：小寫英文，例如 translator、recipe-maker。
- description：給村長看的名片，最重要。一兩句寫清楚能做什麼，並列出使用者常見的說法，格式像：
  「中英互譯。適合「翻譯、翻成英文、中翻英、這句英文是什麼意思」等需求，也能處理前一位村民的成果。」
- skill：一句話說明主要技能。
- examples：3～5 句使用者真的會打的請求。
- keywords：4～8 個詞，請求裡出現它們就代表需要這位村民。要是使用者實際會打的字，而且不要和現有村民重疊。
- tags：1～3 個英文小寫標籤。
- sprite：從可選外觀挑最符合的 id。
- creativity：stable（答案要正確一致，例如翻譯、計算、整理）/ balanced / creative（創作、點子）。
- length：short（300 字內）/ medium（1000 字內）/ long（3000 字內）。
- system_prompt：村民自己的工作守則，用繁體中文，格式：
  第一行「你是村子裡的<name>，<一句個性描述>。」
  接著「規則：」和 4～7 條以「- 」開頭的規則，要包含：做什麼、輸出格式、有參考資料時要以參考資料為素材、
  只輸出成果本身（不要前言、不要解釋、不要結語）、使用者說不能做的事、使用繁體中文（除非任務指定其他語言）。
- tests：3～4 題測試。input 是村長會給的一句任務；context 通常是空字串，測「接在別人後面」時填一段素材。
  contains 只放「任務或素材裡已經出現、結果一定會沿用」的詞（例如素材是清蒸魚，結果要提到清蒸魚），其他一律留空陣列；
  not_contains 放不該出現的前言整句片段（至少 3 個字），例如「以下是」「希望你喜歡」「Here is」，不要放「好的」這種常見短詞；
  lang：結果一定是中文填 zh，一定是英文填 en，不確定填 any。
- summary：用 1～2 句話跟使用者說你打造了什麼樣的村民（親切口吻）。

只輸出 JSON，不要任何說明。"""


def _draft_digest(spec: dict | None) -> dict:
    """草稿太長時只給工匠看重點欄位。"""
    if not spec:
        return {}
    card, v = spec.get("card", {}), spec.get("x-village", {})
    return {
        "name": card.get("name"),
        "id": card.get("id"),
        "description": card.get("description"),
        "examples": [e for s in card.get("skills", []) for e in s.get("examples", [])],
        "keywords": v.get("keywords"),
        "sprite": v.get("sprite"),
        "system_prompt": (spec.get("prompt") or {}).get("system"),
    }


class BlueprintError(ValueError):
    pass


def _strs(data: dict, key: str, lo: int = 0) -> list[str]:
    raw = data.get(key) or []
    if not isinstance(raw, list):
        raise BlueprintError(f"{key} 必須是字串陣列")
    out = list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))
    if len(out) < lo:
        raise BlueprintError(f"{key} 至少要 {lo} 個")
    return out


def _text(data: dict, key: str, lo: int = 1) -> str:
    t = str(data.get(key) or "").strip()
    if len(t) < lo:
        raise BlueprintError(f"{key} 不能是空的" if lo <= 1 else f"{key} 太短（至少 {lo} 字）")
    return t


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-_")
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"^[^a-z]+", "", s)
    return s[:32].rstrip("-_") or "villager"


def unique_id(base: str, taken: set[str]) -> str:
    base = slugify(base)
    if base in RESERVED_IDS:
        base = f"{base}-villager"
    cand, n = base, 2
    while cand in taken:
        cand, n = f"{base}-{n}", n + 1
    return cand


def pick_home(taken: list[tuple[int, int]]) -> tuple[int, int]:
    """挑離其他人最遠的空地（不在道路與廣場上）；結果固定，方便測試。"""
    others = taken + SYSTEM_HOMES
    best, best_d = (3, 4), -1.0
    for y in range(3, MAP_H - 2):
        for x in range(2, MAP_W - 2):
            if y in (ROAD_ROW, ROAD_ROW + 1) or x == ROAD_COL or math.dist((x, y), SYSTEM_HOMES[0]) < 4:
                continue
            d = min(math.dist((x, y), o) for o in others)
            if d > best_d:
                best, best_d = (x, y), d
    return best


def build_spec(
    data: dict, sprites: list[SpriteOption], neighbors: list[Neighbor], draft: dict | None = None
) -> AgentSpec:
    """藍圖 → AgentSpec。藍圖有問題時 raise BlueprintError（訊息會帶回給工匠重做）。"""
    name = _text(data, "name")
    if any(n.name == name for n in neighbors if not draft or n.id != draft["card"]["id"]):
        raise BlueprintError(f"name「{name}」和現有村民重複，請換一個")
    description = _text(data, "description", 10)
    system = _text(data, "system_prompt", 20)
    examples = _strs(data, "examples", 2)
    keywords = _strs(data, "keywords", 2)
    creativity = data.get("creativity") if data.get("creativity") in CREATIVITY else "balanced"
    length = data.get("length") if data.get("length") in LENGTH else "medium"
    max_chars, max_tokens = LENGTH[length]

    sprite_ids = [s.id for s in sprites]
    sprite = data.get("sprite") if data.get("sprite") in sprite_ids else (sprite_ids[0] if sprite_ids else None)

    others = [n for n in neighbors if not draft or n.id != draft["card"]["id"]]
    if draft:  # 修改草稿：id、住處、模型選擇沿用（使用者可能已經手動調過）
        vid = draft["card"]["id"]
        home = draft.get("x-village", {}).get("home")
        model = {**draft.get("model", {}), "temperature": CREATIVITY[creativity], "max_tokens": max_tokens}
    else:
        vid = unique_id(str(data.get("id") or name), {n.id for n in neighbors})
        home = list(pick_home([n.home for n in others if n.home]))
        model = {"tier": "small", "model_id": None, "temperature": CREATIVITY[creativity], "max_tokens": max_tokens}

    tests = []
    for t in (data.get("tests") or [])[:5]:
        if not isinstance(t, dict) or not str(t.get("input") or "").strip():
            continue
        lang = t.get("lang") if t.get("lang") in ("zh", "en") else None
        given = f"{t['input']}\n{t.get('context') or ''}"
        tests.append({
            "input": str(t["input"]).strip(),
            "context": str(t.get("context") or "").strip(),
            "expect": {
                # LLM 常猜「結果應該會有」的詞（例如寫月亮一定有 moon），其實不一定；只留任務 / 素材裡就有的詞
                "contains": [w for w in _strs(t, "contains") if w in given],
                # 「好的」「希望」這種短詞在正常回答裡也常出現（最好的、希望你…），會誤判
                "not_contains": [w for w in _strs(t, "not_contains") if len(w) >= 3],
                "lang": lang,
                "max_chars": max_chars,
            },
        })

    doc = {
        "card": {
            "id": vid,
            "name": name,
            "description": description,
            "version": "0.1.0",
            "skills": [{"id": "main", "description": str(data.get("skill") or description).strip(), "examples": examples}],
            "tags": _strs(data, "tags")[:5],
        },
        "x-village": {"sprite": sprite, "home": home, "keywords": keywords},
        "kind": "prompt",
        "model": model,
        "prompt": {"system": system, "user_template": DEFAULT_TEMPLATE},
        "validate": {"must_not_be_empty": True, "max_chars": max_chars},
        "tests": tests,
    }
    try:
        return AgentSpec.model_validate(doc)
    except ValueError as e:
        raise BlueprintError(str(e)) from None


def fallback_spec(
    messages: list[ChatMessage], sprites: list[SpriteOption], neighbors: list[Neighbor], draft: dict | None
) -> AgentSpec:
    """LLM 不能用時：用使用者說過的話拼出基本草稿，讓使用者在編輯器裡補完。"""
    if draft:
        return AgentSpec.model_validate(draft)
    said = [m.content.strip() for m in messages if m.role == "user" and m.content.strip()]
    first = said[0] if said else "新村民"
    name = "新村民"
    while any(n.name == name for n in neighbors):
        name += "＋"
    rules = "".join(f"- {x[:200]}\n" for x in said[:5])
    return AgentSpec.model_validate({
        "card": {
            "id": unique_id("villager", {n.id for n in neighbors}),
            "name": name,
            "description": f"{first[:120]}（工匠的備用流程產生，請補上使用者常見的說法）",
            "skills": [{"id": "main", "description": first[:60], "examples": said[:3]}],
        },
        "x-village": {
            "sprite": sprites[0].id if sprites else None,
            "home": list(pick_home([n.home for n in neighbors if n.home])),
            "keywords": [],  # 寧可空白讓使用者填，也不要亂猜
        },
        "model": {"tier": "small", "temperature": 0.6, "max_tokens": 2048},
        "prompt": {
            "system": f"你是村子裡的{name}。\n規則：\n{rules}- 有參考資料時，以參考資料為素材。\n"
            "- 只輸出成果本身，不要前言或解釋。\n- 使用繁體中文，除非任務指定其他語言。",
            "user_template": DEFAULT_TEMPLATE,
        },
        "validate": {"must_not_be_empty": True, "max_chars": 1000},
    })


async def generate(
    llm: ChatLLM,
    messages: list[ChatMessage],
    sprites: list[SpriteOption],
    neighbors: list[Neighbor],
    *,
    draft: dict | None = None,
    target: str = "tier:large",
) -> Draft:
    notes: list[str] = []
    convo = "\n".join(f"{'使用者' if m.role == 'user' else '工匠'}：{m.content}" for m in messages)
    msgs = [ChatMessage("user", f"對話內容：\n{convo}\n\n請輸出村民藍圖 JSON。")]
    schema = blueprint_schema([s.id for s in sprites])
    system = generate_system_prompt(sprites, neighbors, draft)
    for attempt in range(GEN_RETRIES + 1):
        try:
            r = await llm.chat(target, msgs, system=system, json_schema=schema, max_tokens=4096)
        except LLMError as e:
            notes.append(f"工匠的 LLM 無法使用：{e}")
            break
        try:
            data = _extract_json(r.text)
            spec = build_spec(data, sprites, neighbors, draft)
            summary = str(data.get("summary") or "").strip() or f"做好了！這是「{spec.name}」的草稿。"
            return Draft(spec=spec.to_doc(), notes=notes, summary=summary, model=r.model)
        except (PlanError, BlueprintError) as e:
            notes.append(f"第 {attempt + 1} 次藍圖有問題：{e}，請工匠重做")
            msgs += [
                ChatMessage("assistant", r.text or "（空白）"),
                ChatMessage("user", f"這份藍圖有問題：{e}。請修正後重新輸出完整 JSON。"),
            ]
    spec = fallback_spec(messages, sprites, neighbors, draft)
    summary = (
        "抱歉，我現在沒辦法動手改，先保留原本的草稿。"
        if draft
        else "抱歉，我現在沒辦法好好打造，先用你說的話拼了一份基本草稿，請在編輯器裡補完描述、關鍵字和工作守則。"
    )
    return Draft(spec=spec.to_doc(), notes=notes, summary=summary, source="fallback")
