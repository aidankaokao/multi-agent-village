"""書記：問答結束後，從使用者說的話裡記下「關於使用者本人、長期有效」的事，也處理「忘記…」的要求。

和村長、工匠、審核員一樣是系統角色（id: scribe）。為了省 token、不亂記：
  - 句子裡沒有跟自己有關的線索（我、我的、以後、記住、忘記…）就不呼叫 LLM
  - 結構化輸出 {add: [{content, kind}], forget: [編號]}；既有記憶用編號列給它，弱模型比較不會寫錯 id
  - 一次最多記 3 條，只記使用者自己說的，不記一次性的任務內容
  - LLM 不能用時退回固定句型（我叫…、我喜歡…、以後都…）
"""

import logging
import re
from dataclasses import dataclass, field

from village.core.orchestrator import ChatLLM, PlanError, _extract_json
from village.llm.base import ChatMessage, LLMError

log = logging.getLogger("village.scribe")

SCRIBE_ID = "scribe"
MAX_NEW = 3

_PERSONAL = re.compile(
    r"我|俺|本人|my |\bI\b|I'm|I am|以後|之後都|往後|每次都|記住|記得|別忘|忘記|忘掉|不要再|不用再|叫我|稱呼我", re.I
)


def worth_checking(message: str) -> bool:
    return bool(_PERSONAL.search(message))


SCHEMA = {
    "type": "object",
    "properties": {
        "add": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"content": {"type": "string"}, "kind": {"type": "string", "enum": ["preference", "fact"]}},
                "required": ["content", "kind"],
                "additionalProperties": False,
            },
        },
        "forget": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["add", "forget"],
    "additionalProperties": False,
}

SYSTEM = """你是村子裡的書記，負責替使用者記住「關於他本人、長期有效」的事，讓村民以後更懂他。

要記的（add）：
- fact：使用者的名字、稱呼、職業、居住地、生日、家人寵物、長期的興趣等事實
- preference：使用者對回答的長期偏好（例如「回答簡短一點」「用條列」「不要用表情符號」「叫我小艾」）
規則：
1. 只記使用者「自己說的、關於自己」的事；不記一次性的任務內容（例如「寫一首海的詩」不用記），不記村民回答的內容。
2. 用第三人稱、一句話寫清楚，例如「使用者叫 Aidan」「使用者希望回答簡短」。
3. 已經記過的（見「目前的記憶」）不要重複；資訊有更新時，把舊的放進 forget、新的放進 add。
4. 使用者要求忘記某件事、或說之前的資訊不對時，把對應的編號放進 forget。
5. 最多記 3 條；沒有值得記的就回傳空陣列。用繁體中文。
只輸出 JSON：{"add": [{"content": "...", "kind": "fact 或 preference"}], "forget": [編號]}"""


@dataclass
class Notes:
    add: list[tuple[str, str]] = field(default_factory=list)  # (content, kind)
    forget: list[str] = field(default_factory=list)  # 記憶 id
    source: str = "llm"  # llm / rule / skip


def render(message: str, existing: list[tuple[str, str]]) -> str:
    lines = [f"{i}. {c}" for i, (_, c) in enumerate(existing, 1)] or ["（還沒有）"]
    return "目前的記憶：\n" + "\n".join(lines) + f"\n\n使用者剛剛說：{message}"


# ---------------------------------------------------------------- 退路：固定句型

_RULES = [
    (re.compile(r"(?:我叫|我的名字(?:是|叫)|叫我)\s*([^\s，。,.!！?？]{1,20})"), "fact", "使用者叫 {}"),
    (re.compile(r"我的生日是\s*([^\s，。,.!！?？]{1,20})"), "fact", "使用者的生日是 {}"),
    (re.compile(r"我住在\s*([^\s，。,.!！?？]{1,20})"), "fact", "使用者住在{}"),
    (re.compile(r"我(?:很|最)?(?<!不)喜歡\s*([^\s，。,.!！?？]{1,20})"), "fact", "使用者喜歡{}"),
    (re.compile(r"我不喜歡\s*([^\s，。,.!！?？]{1,20})"), "preference", "使用者不喜歡{}"),
    (re.compile(r"(?:以後|之後|往後)(?:都|請)?\s*([^。！!？?]{2,40})"), "preference", "使用者希望以後{}"),
]


def by_rules(message: str) -> Notes:
    notes = Notes(source="rule")
    for pattern, kind, template in _RULES:
        if m := pattern.search(message):
            notes.add.append((template.format(m.group(1).strip()), kind))
    notes.add = notes.add[:MAX_NEW]
    return notes


async def take_notes(
    llm: ChatLLM, message: str, existing: list[tuple[str, str]], target: str = "tier:small"
) -> Notes:
    """existing：[(id, content)]。回傳要新增與要刪除的記憶。"""
    if not worth_checking(message):
        return Notes(source="skip")
    try:
        r = await llm.chat(
            target, [ChatMessage("user", render(message, existing))], system=SYSTEM, json_schema=SCHEMA,
            temperature=0, max_tokens=512, fallback="tier:large",
        )
        data = _extract_json(r.text)
    except (LLMError, PlanError) as e:
        log.info("書記的 LLM 不能用，改用固定句型：%s", e)
        return by_rules(message)
    notes = Notes()
    for item in (data.get("add") or [])[:MAX_NEW]:
        if isinstance(item, dict) and str(item.get("content") or "").strip():
            kind: str = item["kind"] if item.get("kind") in ("preference", "fact") else "fact"
            notes.add.append((str(item["content"]).strip(), kind))
    for n in data.get("forget") or []:
        if isinstance(n, int) and 1 <= n <= len(existing):
            notes.forget.append(existing[n - 1][0])
    return notes


def render_memories(items: list[str]) -> str:
    """帶進村民參考資料的格式。"""
    lines = "\n".join(f"- {c}" for c in items)
    return f"關於使用者（書記記下的長期記憶，回答時參考，不必特別提起這段說明）：\n{lines}"
