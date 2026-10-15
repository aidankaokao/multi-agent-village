"""村長（orchestrator）：把使用者訊息變成 Plan。

只做「選誰、給什麼指示、用哪一步的成果」；實際執行交給 executor。
為了讓弱模型也能用，依序嘗試：
  1. @指名        —— 使用者直接寫 @poet … → @jester …，完全不靠 LLM
  2. LLM 規劃     —— JSON schema 約束輸出，解析/驗證失敗會帶錯誤訊息重試
  3. 關鍵字備援   —— 依 agent.yaml 的 x-village.keywords 在訊息中的出現順序串接
  4. 村長直接回覆 —— 沒有村民適合（例如打招呼）
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from village.core.card import AgentSpec
from village.llm.base import ChatMessage, ChatResult, LLMError

log = logging.getLogger("village.mayor")

MAX_STEPS = 5
PLAN_RETRIES = 2
PlanSource = Literal["mention", "llm", "keyword", "direct"]


class ChatLLM(Protocol):
    async def chat(self, target: str, messages: list[ChatMessage], **kw) -> ChatResult: ...


@dataclass
class Step:
    id: str
    agent: str
    instruction: str
    use: list[str] = field(default_factory=list)


@dataclass
class Plan:
    steps: list[Step]
    source: PlanSource
    notes: list[str] = field(default_factory=list)  # 規劃過程中的退路紀錄，顯示在 trace

    def to_dict(self) -> dict:
        return {"steps": [asdict(s) for s in self.steps], "source": self.source, "notes": self.notes}


class PlanError(ValueError):
    pass


NoteCallback = Callable[[str], Awaitable[None]]


class Notes(list[str]):
    """規劃過程的紀錄；有 on_note 時同時即時回報，讓前端在等待時看得到村長在做什麼。"""

    def __init__(self, on_note: NoteCallback | None = None):
        super().__init__()
        self._on_note = on_note

    async def add(self, note: str) -> None:
        self.append(note)
        if self._on_note:
            await self._on_note(note)


# ---------------------------------------------------------------- 1. @指名

_MENTION = re.compile(r"@([a-z][a-z0-9_-]*)")
_EDGE_PUNCT = re.compile(r"^[\s,，、;；:：→>＞=\-]+|[\s,，、;；:：→>＞=\-]+$")
_TRAILING_CONNECTOR = re.compile(r"(然後|接著|之後|再來|再|並且|並|and then|then)$", re.I)


def _clean_segment(text: str) -> str:
    """去掉 @指名 片段頭尾的標點與連接詞，例如「寫一首詩然後 →」→「寫一首詩」。"""
    prev = None
    while prev != text:
        prev = text
        text = _TRAILING_CONNECTOR.sub("", _EDGE_PUNCT.sub("", text))
    return text


def plan_from_mentions(message: str, agents: dict[str, AgentSpec]) -> Plan | None:
    mentions = [m for m in _MENTION.finditer(message) if m.group(1) in agents]
    if not mentions:
        return None
    prefix = _clean_segment(message[: mentions[0].start()])
    steps: list[Step] = []
    for i, m in enumerate(mentions):
        end = mentions[i + 1].start() if i + 1 < len(mentions) else len(message)
        text = _clean_segment(message[m.end() : end])
        if i == 0 and prefix:
            text = f"{prefix} {text}".strip()
        spec = agents[m.group(1)]
        if not text:
            skill = spec.card.skills[0].description if spec.card.skills else spec.card.description
            text = message if i == 0 else f"以參考資料為素材：{skill}"
        steps.append(Step(id=f"s{i + 1}", agent=spec.id, instruction=text, use=[f"s{i}"] if i else []))
    return Plan(steps=steps[:MAX_STEPS], source="mention")


# ---------------------------------------------------------------- 2. LLM 規劃


def plan_schema(agent_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "agent": {"type": "string", "enum": agent_ids},
                        "instruction": {"type": "string"},
                        "use": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "agent", "instruction", "use"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["steps"],
        "additionalProperties": False,
    }


def planner_system_prompt(agents: list[AgentSpec]) -> str:
    lines = []
    for a in agents:
        examples = "；".join(e for s in a.card.skills for e in s.examples[:3])
        lines.append(f"- {a.id}（{a.name}）：{a.card.description}" + (f" 例：{examples}" if examples else ""))
    roster = "\n".join(lines)
    return f"""你是村長，負責把使用者的請求分派給適合的村民（agent）。

可用的村民：
{roster}

規則：
1. 只安排使用者「明確要求」的工作，絕對不要自己加步驟。使用者只要一件事，就只用一位村民、一個步驟。
2. agent 只能使用上面列出的 id。
3. 依請求的先後順序安排步驟，id 依序為 s1、s2、s3…。
4. 後面的步驟若需要前面的成果，在 use 填入那些步驟的 id；不需要就填空陣列。
5. instruction 用一句話寫清楚該村民要做的事，只能使用請求裡的資訊（主題、風格、形式），不要自行添加。
6. 最多 {MAX_STEPS} 步。
7. 如果沒有任何村民適合（例如打招呼、閒聊、詢問村子），steps 回傳空陣列。
只輸出 JSON，不要任何說明。

範例
請求：講一個關於狗的笑話
輸出：{{"steps":[{{"id":"s1","agent":"<負責笑話的村民 id>","instruction":"講一個關於狗的笑話","use":[]}}]}}

請求：寫一首關於海的詩，再改成笑話
輸出：{{"steps":[{{"id":"s1","agent":"<負責寫詩的村民 id>","instruction":"寫一首關於海的詩","use":[]}},{{"id":"s2","agent":"<負責笑話的村民 id>","instruction":"把這首詩改編成笑話","use":["s1"]}}]}}

請求：你好
輸出：{{"steps":[]}}"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if m := re.search(r"```(?:json)?\s*(.*?)```", text, re.S):
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise PlanError("找不到 JSON 物件")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise PlanError(f"JSON 格式錯誤：{e.msg}") from None


def parse_plan(text: str, agents: dict[str, AgentSpec]) -> list[Step]:
    data = _extract_json(text)
    raw = data.get("steps")
    if not isinstance(raw, list):
        raise PlanError("缺少 steps 陣列")
    if len(raw) > MAX_STEPS:
        raise PlanError(f"步驟太多（{len(raw)}），最多 {MAX_STEPS} 步")
    steps: list[Step] = []
    seen: set[str] = set()
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            raise PlanError(f"第 {i + 1} 步不是物件")
        sid = str(s.get("id") or f"s{i + 1}")
        agent = s.get("agent")
        instruction = str(s.get("instruction") or "").strip()
        use = s.get("use") or []
        if agent not in agents:
            raise PlanError(f"{sid} 的 agent「{agent}」不存在，只能用：{', '.join(agents)}")
        if not instruction:
            raise PlanError(f"{sid} 缺少 instruction")
        if sid in seen:
            raise PlanError(f"步驟 id「{sid}」重複")
        if not isinstance(use, list) or any(u not in seen for u in use):
            raise PlanError(f"{sid} 的 use 只能引用前面的步驟：{sorted(seen) or '（無）'}")
        seen.add(sid)
        steps.append(Step(id=sid, agent=agent, instruction=instruction, use=[str(u) for u in use]))
    return steps


def prune_unrequested(steps: list[Step], message: str, agents: dict[str, AgentSpec], notes: list[str]) -> list[Step]:
    """弱模型常會「多做」：例如只要詩卻順手加一步笑話。

    移除這種步驟：該村民有設定關鍵字、但請求裡一個都沒出現，而且沒有其他步驟依賴它。
    至少保留一步；沒設定關鍵字的村民不受影響。
    """
    lower = message.lower()

    def unrequested(s: Step) -> bool:
        kws = agents[s.agent].village.keywords
        return bool(kws) and not any(k.lower() in lower for k in kws)

    steps = list(steps)
    changed = True
    while changed and len(steps) > 1:
        changed = False
        used = {u for s in steps for u in s.use}
        for s in reversed(steps):
            if s.id not in used and unrequested(s):
                steps.remove(s)
                notes.append(f"移除使用者沒要求的步驟：{s.id} {agents[s.agent].name}「{s.instruction}」")
                changed = True
                break
    return steps


async def plan_with_llm(message: str, agents: list[AgentSpec], llm: ChatLLM, notes: Notes) -> list[Step] | None:
    by_id = {a.id: a for a in agents}
    messages = [ChatMessage("user", f"請求：{message}")]
    for attempt in range(PLAN_RETRIES + 1):
        try:
            r = await llm.chat(
                "tier:large",
                messages,
                system=planner_system_prompt(agents),
                json_schema=plan_schema(list(by_id)),
                max_tokens=1024,
            )
        except LLMError as e:
            await notes.add(f"LLM 規劃無法使用：{e}")
            return None
        try:
            return prune_unrequested(parse_plan(r.text, by_id), message, by_id, notes)
        except PlanError as e:
            await notes.add(f"第 {attempt + 1} 次規劃無效：{e}，請村長重新規劃")
            messages += [
                ChatMessage("assistant", r.text or "（空白）"),
                ChatMessage("user", f"這個計畫有問題：{e}。請修正後重新輸出完整 JSON。"),
            ]
    return None


# ---------------------------------------------------------------- 3. 關鍵字備援


def plan_from_keywords(message: str, agents: list[AgentSpec]) -> list[Step]:
    lower = message.lower()
    found: list[tuple[int, AgentSpec]] = []
    for a in agents:
        positions = [lower.find(k.lower()) for k in a.village.keywords if k and k.lower() in lower]
        if positions:
            found.append((min(positions), a))
    found.sort(key=lambda x: x[0])
    steps = []
    for i, (_, a) in enumerate(found[:MAX_STEPS]):
        instruction = message if len(found) == 1 else f"{message}（你只負責其中屬於「{a.name}」的部分）"
        steps.append(Step(id=f"s{i + 1}", agent=a.id, instruction=instruction, use=[f"s{i}"] if i else []))
    return steps


# ---------------------------------------------------------------- 入口


async def make_plan(
    message: str, agents: list[AgentSpec], llm: ChatLLM, on_note: NoteCallback | None = None
) -> Plan:
    by_id = {a.id: a for a in agents}
    if plan := plan_from_mentions(message, by_id):
        return plan
    if not agents:
        return Plan(steps=[], source="direct", notes=["村裡還沒有任何村民"])

    notes = Notes(on_note)
    steps = await plan_with_llm(message, agents, llm, notes)
    if steps is not None:
        return Plan(steps=steps, source="llm" if steps else "direct", notes=list(notes))

    steps = plan_from_keywords(message, agents)
    if steps:
        await notes.add("改用關鍵字備援規劃")
        return Plan(steps=steps, source="keyword", notes=list(notes))
    return Plan(steps=[], source="direct", notes=list(notes))


async def direct_reply(
    message: str, agents: list[AgentSpec], llm: ChatLLM, on_text: Callable[[str], Awaitable[None]] | None = None
) -> str:
    roster = "、".join(f"{a.name}（@{a.id}）" for a in agents) or "（目前沒有村民）"
    system = (
        "你是村子的村長，個性親切。用繁體中文簡短回答（3 句以內）。"
        f"村裡目前有這些村民可以幫忙：{roster}。"
        "如果使用者的需求村民做不到，就誠實說明，並介紹村民能做什麼。"
    )
    try:
        kw = {"on_text": on_text} if on_text else {}
        r = await llm.chat("tier:large", [ChatMessage("user", message)], system=system, max_tokens=400, **kw)
        if text := re.sub(r"<think>.*?</think>", "", r.text, flags=re.S).strip():
            return text
    except LLMError as e:
        log.warning("村長直接回覆失敗：%s", e)
    return f"你好！我是村長。村裡目前有：{roster}。試試看請他們幫忙吧！"
