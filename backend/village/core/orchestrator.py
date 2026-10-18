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
from village.core import loop as loops
from village.core.context import PREV, Turn, refers_back, render_history
from village.llm.base import ChatMessage, ChatResult, LLMError
from village.tools import TOOLS

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
    loop: loops.Loop | None = None  # 改到過關為止（最多一個）

    def to_dict(self) -> dict:
        return {"steps": [asdict(s) for s in self.steps], "source": self.source, "notes": self.notes,
                "loop": self.loop.to_dict() if self.loop else None}


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
            },
            "loop": {
                "type": ["object", "null"],
                "properties": {
                    "check": {"type": "string"},
                    "back_to": {"type": "string"},
                    "max_rounds": {"type": "integer"},
                },
                "required": ["check", "back_to", "max_rounds"],
                "additionalProperties": False,
            },
        },
        "required": ["steps", "loop"],
        "additionalProperties": False,
    }


def is_costly(spec: AgentSpec | None) -> bool:
    """每次執行都要花錢的村民（例如畫家）：規劃時特別提醒、多餘的步驟要刪掉。"""
    return bool(spec and spec.kind == "tool" and spec.tool and spec.tool.id in TOOLS and TOOLS[spec.tool.id].costly)


def render_documents(docs, fresh: bool, doc_agents: list[AgentSpec]) -> str:
    """附的文件（給村長規劃用）：只列檔名，內容村子不讀，只交給收文件的駐村專家。"""
    names = "、".join(d.describe() for d in docs)
    who = "、".join(f"{a.id}（{a.name}）" for a in doc_agents) or "（目前沒有在村的收文件村民）"
    when = "使用者這次附了文件" if fresh else "前面的對話附過文件（這次沒有再附）"
    return (
        f"{when}：{names}。文件內容只有收文件的村民 {who} 拿得到，其他村民看不到。"
        + ("整理、彙整、分析這些文件或做報告就派給它，instruction 寫清楚使用者要的重點方向。" if fresh else
           "這次的請求如果是在追問那些文件或報告（例如「第二段寫詳細一點」「改成著重成本」），就再派給它；無關就不用。")
    )


def planner_system_prompt(
    agents: list[AgentSpec], history: list[Turn] | None = None, appraisal: str = "", documents: str = "", disabled: str = "",
) -> str:
    lines = []
    for a in agents:
        examples = "；".join(e for s in a.card.skills for e in s.examples[:3])
        costly = "【每次都要花錢：使用者沒有明確要求畫好幾張時，只安排一個步驟】" if is_costly(a) else ""
        tool = TOOLS.get(a.tool.id) if a.kind == "tool" and a.tool else None
        hint = f"（{tool.planner_hint}）" if tool and tool.planner_hint else ""
        if a.kind == "remote" and a.remote and a.remote.accepts:
            hint += f"【收文件：{'、'.join(a.remote.accepts)}；只有它讀得到使用者附的文件】"
        lines.append(f"- {a.id}（{a.name}）：{a.card.description}{hint}{costly}" + (f" 例：{examples}" if examples else ""))
    roster = "\n".join(lines)
    if disabled:
        roster += (
            f"\n（停用中，不能派工：{disabled}。請求的工作只有停用中的村民會做時，那一步不要排、"
            "也不要找別的村民代替；整個請求都做不了就回傳空陣列，村長會告訴使用者）"
        )
    history_rule = history_block = ""
    if history:
        history_rule = (
            f"\n9. 請求如果是在說前面的內容（它、這個、剛剛、上面、再把…），那一步的 use 填 \"{PREV}\"（代表上一輪的回答），"
            "instruction 寫清楚要對它做什麼；請求沒提到前面的內容就不要用。"
        )
        history_block = (
            f"\n\n請求：（上一輪回答是一首詩）再把它翻成英文\n"
            f'輸出：{{{{"steps":[{{{{"id":"s1","agent":"<負責翻譯的村民 id>","instruction":"把上一輪的詩翻成英文","use":["{PREV}"]}}}}],"loop":null}}}}'
            f"\n\n最近的對話（舊 → 新）：\n{render_history(history)}"
        )
    image_rule = image_block = ""
    if appraisal:
        image_rule = (
            "\n10. 使用者附了圖片，鑑定師已經看過並寫下內容（見最下方）。每位村民都會自動拿到這段內容，不要安排「看圖」的步驟。"
            "只是問圖片本身（這是什麼、圖裡寫什麼、有幾個…）就回傳空陣列，由村長根據鑑定結果回答；"
            "要對圖片內容做事（翻譯、計算、畫圖表、寫詩…）才派村民，instruction 寫清楚要對圖片的哪些內容做什麼。"
        )
        image_block = f"\n\n{appraisal}"
    if documents:
        image_rule += f"\n11. {documents}"
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
8. loop：只有請求要求「審查不通過就重做 / 改到通過為止 / 最多 N 輪」時才填，否則填 null。
   填法：安排一個審查步驟（use 填被審查的步驟），loop 填 {{"check": 審查步驟 id, "back_to": 不通過時退回的步驟 id
   （只要改寫就填被審查的那一步；要重新查資料就填第一步）, "max_rounds": N（沒說就填 3）}}。{history_rule}{image_rule}
只輸出 JSON，不要任何說明。

範例
請求：講一個關於狗的笑話
輸出：{{"steps":[{{"id":"s1","agent":"<負責笑話的村民 id>","instruction":"講一個關於狗的笑話","use":[]}}],"loop":null}}

請求：寫一首關於海的詩，再改成笑話
輸出：{{"steps":[{{"id":"s1","agent":"<負責寫詩的村民 id>","instruction":"寫一首關於海的詩","use":[]}},{{"id":"s2","agent":"<負責笑話的村民 id>","instruction":"把這首詩改編成笑話","use":["s1"]}}],"loop":null}}

請求：寫一首關於海的詩，請審稿的村民檢查有沒有押韻，不通過就重寫，最多 3 輪
輸出：{{"steps":[{{"id":"s1","agent":"<負責寫詩的村民 id>","instruction":"寫一首關於海的詩","use":[]}},{{"id":"s2","agent":"<負責審稿的村民 id>","instruction":"檢查這首詩有沒有押韻","use":["s1"]}}],"loop":{{"check":"s2","back_to":"s1","max_rounds":3}}}}

請求：你好
輸出：{{"steps":[],"loop":null}}{history_block}{image_block}"""


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


def parse_plan(text: str, agents: dict[str, AgentSpec], allow_prev: bool = False) -> list[Step]:
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
        ok_refs = seen | ({PREV} if allow_prev else set())
        if not isinstance(use, list) or any(u not in ok_refs for u in use):
            raise PlanError(f"{sid} 的 use 只能引用前面的步驟：{sorted(ok_refs) or '（無）'}")
        seen.add(sid)
        steps.append(Step(id=sid, agent=agent, instruction=instruction, use=[str(u) for u in use]))
    return steps


def parse_loop(text: str) -> loops.Loop | None:
    """LLM 計畫裡的 loop（格式不對就當作沒有，之後還有不靠 LLM 的判斷）。"""
    try:
        raw = _extract_json(text).get("loop")
    except PlanError:
        return None
    if not isinstance(raw, dict) or not raw.get("check") or not raw.get("back_to"):
        return None
    try:
        return loops.Loop(str(raw["check"]), str(raw["back_to"]), int(raw.get("max_rounds") or 0))
    except (TypeError, ValueError):
        return None


def finish_loop(steps: list[Step], message: str, loop: loops.Loop | None, notes: list[str]) -> loops.Loop | None:
    """確認 LLM 給的迴圈合理；沒有（或不合理）但請求像在要求「不通過就重做」時，由程式補上。"""
    checked = loops.validate(loop, steps)
    if loop and not checked:
        by_id = {s.id: s for s in steps}
        check = by_id.get(loop.check)
        if check and any(by_id[u].agent == check.agent for u in check.use if u in by_id):
            notes.append(f"審查者和被審查的是同一位村民（{check.agent}），自己審自己沒有意義，不排迴圈")
        else:
            notes.append(f"村長規劃的迴圈不合理（{loop.check} 退回 {loop.back_to}），改由程式判斷")
    if checked is None and (guess := loops.from_message(steps, message)):
        checked = loops.validate(guess, steps)
        if checked:
            notes.append(f"請求要求不通過就重做：{checked.check} 審查，不通過退回 {checked.back_to}")
    return checked


def prune_unrequested(
    steps: list[Step], message: str, agents: dict[str, AgentSpec], notes: list[str], keep: set[str] | None = None,
) -> list[Step]:
    """弱模型常會「多做」：例如只要詩卻順手加一步笑話。

    移除這種步驟（而且沒有其他步驟依賴它）：
      - 該村民有設定關鍵字、但請求裡一個都沒出現，指示也不是請求裡的原話
      - 會花錢的村民（畫家）重複出現：第二步以後的指示不是請求裡的原話（例如把鑑定結果又畫一次）
    至少保留一步；沒設定關鍵字的村民不受影響。
    """
    lower = message.lower()

    def unrequested(s: Step) -> bool:
        if keep and s.id in keep:  # 迴圈的審查步驟是請求要的
            return False
        if _mentioned(s.instruction, message):  # 「畫一隻狗，再畫一隻兔子」的兩步都是原話
            return False
        kws = agents[s.agent].village.keywords
        return bool(kws) and not any(k.lower() in lower for k in kws)

    def extra_costly(s: Step) -> bool:
        if not is_costly(agents[s.agent]) or _mentioned(s.instruction, message):
            return False
        return any(o.agent == s.agent for o in steps[: steps.index(s)])

    steps = list(steps)
    changed = True
    while changed and len(steps) > 1:
        changed = False
        used = {u for s in steps for u in s.use}
        for s in reversed(steps):
            if s.id not in used and (unrequested(s) or extra_costly(s)):
                steps.remove(s)
                notes.append(f"移除使用者沒要求的步驟：{s.id} {agents[s.agent].name}「{s.instruction}」")
                changed = True
                break
    return steps


_PUNCT = re.compile(r"[\s，。、；：,.;:!?！？「」『』（）()\"'~～\-—]+")


def _bigrams(text: str) -> set[str]:
    t = _PUNCT.sub("", text.lower())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _mentioned(instruction: str, message: str) -> bool:
    """步驟的指示是不是請求裡的原話（字的兩兩組合有六成以上出現在請求裡）。"""
    b = _bigrams(instruction)
    return bool(b) and len(b & _bigrams(message)) / len(b) >= 0.6


async def plan_with_llm(
    message: str, agents: list[AgentSpec], llm: ChatLLM, notes: Notes, history: list[Turn] | None = None,
    appraisal: str = "",
    documents: str = "",
    disabled: str = "",
) -> tuple[list[Step], loops.Loop | None] | None:
    by_id = {a.id: a for a in agents}
    messages = [ChatMessage("user", f"請求：{message}")]
    for attempt in range(PLAN_RETRIES + 1):
        try:
            r = await llm.chat(
                "tier:large",
                messages,
                system=planner_system_prompt(agents, history, appraisal, documents, disabled),
                json_schema=plan_schema(list(by_id)),
                max_tokens=1024,
            )
        except LLMError as e:
            await notes.add(f"LLM 規劃無法使用：{e}")
            return None
        try:
            steps = parse_plan(r.text, by_id, allow_prev=bool(history))
            loop = parse_loop(r.text)
            steps = prune_unrequested(steps, message, by_id, notes, keep={loop.check} if loop else None)
            return steps, finish_loop(steps, message, loop, notes)
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


def _link_previous(steps: list[Step], message: str, history: list[Turn] | None, notes: list[str]) -> None:
    """不經 LLM 的規劃（@指名、關鍵字）：請求在指前面的內容時，第一步自動帶上一輪的回答。"""
    if history and steps and not steps[0].use and refers_back(message):
        steps[0].use = [PREV]
        notes.append("請求提到前面的內容，帶入上一輪的回答")


async def make_plan(
    message: str,
    agents: list[AgentSpec],
    llm: ChatLLM,
    on_note: NoteCallback | None = None,
    history: list[Turn] | None = None,
    appraisal: str = "",
    documents: str = "",
    disabled: str = "",
) -> Plan:
    by_id = {a.id: a for a in agents}
    if plan := plan_from_mentions(message, by_id):
        _link_previous(plan.steps, message, history, plan.notes)
        plan.loop = finish_loop(plan.steps, message, None, plan.notes)
        return plan
    if not agents:
        return Plan(steps=[], source="direct", notes=["村裡還沒有任何村民"])

    notes = Notes(on_note)
    planned = await plan_with_llm(message, agents, llm, notes, history, appraisal, documents, disabled)
    if planned is not None:
        steps, loop = planned
        return Plan(steps=steps, source="llm" if steps else "direct", notes=list(notes), loop=loop)

    steps = plan_from_keywords(message, agents)
    if steps:
        await notes.add("改用關鍵字備援規劃")
        _link_previous(steps, message, history, notes)
        loop = finish_loop(steps, message, None, notes)
        return Plan(steps=steps, source="keyword", notes=list(notes), loop=loop)
    return Plan(steps=[], source="direct", notes=list(notes))


async def direct_reply(
    message: str,
    agents: list[AgentSpec],
    llm: ChatLLM,
    on_text: Callable[[str], Awaitable[None]] | None = None,
    history: list[Turn] | None = None,
    memories: str = "",
    appraisal: str = "",
    appraisal_text: str = "",
    disabled: str = "",
) -> str:
    """appraisal：鑑定師看圖的結果（給 LLM 的參考資料）；appraisal_text：LLM 不能用時直接交出鑑定內容。"""
    roster = "、".join(f"{a.name}（@{a.id}）" for a in agents) or "（目前沒有村民）"
    if appraisal:
        # 有圖：直接回答圖片的問題。弱模型容易把圖裡的店名當成自己、或一直推銷村民，要講清楚
        system = (
            "你是村子的村長。根據下方鑑定師對圖片的描述，用繁體中文直接回答使用者的問題：重點先講，清楚簡潔；"
            "需要時可以列出圖裡的文字或數字。不要自我介紹、不要介紹或推薦村民；"
            "圖片裡的店名、人名、文字都是圖片的內容，不是你的身分。"
        )
    else:
        system = (
            "你是村子的村長，個性親切。用繁體中文簡短回答（3 句以內）。"
            f"村裡目前有這些村民可以幫忙：{roster}。"
            "如果使用者的需求村民做不到，就誠實說明，並介紹村民能做什麼。"
        )
        if disabled:
            system += (
                f"目前停用中的村民：{disabled}。使用者要的工作需要停用中的村民時，明確告訴他"
                "「○○目前停用中，請管理員到右上 ⚙️ →「村民」頁啟用」。"
            )
    if memories:
        system += f"\n\n{memories}"
    if appraisal:
        system += f"\n\n{appraisal}"
    try:
        kw = {"on_text": on_text} if on_text else {}
        msgs: list[ChatMessage] = []
        for t in history or []:  # 前幾輪當成真的對話，村長才能回答「我剛剛問了什麼」
            msgs += [ChatMessage("user", t.user), ChatMessage("assistant", t.answer)]
        msgs.append(ChatMessage("user", message))
        r = await llm.chat("tier:large", msgs, system=system, max_tokens=1500 if appraisal else 400, **kw)
        if text := re.sub(r"<think>.*?</think>", "", r.text, flags=re.S).strip():
            return text
    except LLMError as e:
        log.warning("村長直接回覆失敗：%s", e)
    if appraisal_text:  # 村長的 LLM 不能用，但鑑定師看過圖：直接交出鑑定內容
        return f"鑑定師看過圖片，內容如下：\n\n{appraisal_text}"
    return f"你好！我是村長。村裡目前有：{roster}。試試看請他們幫忙吧！"
