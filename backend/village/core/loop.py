"""迴圈：改到過關為止。

計畫裡最多一個迴圈：{check: 審查步驟, back_to: 不通過時退回的步驟, max_rounds: 最多幾輪}。
審查步驟是普通步驟（任何村民或駐村專家），程式在指示後面補「最後一行寫結論」；
不通過 → 從 back_to 重做到審查步驟（重做的步驟拿到上一版成果與審查意見）→ 再審，直到通過或用完輪數。

判定通過：先讀「結論：通過 / 不通過」→ 讀不到就請小模型判 → 也判不出來就當作通過並註明（不會無限重做）。
村長 LLM 常漏掉迴圈，所以請求裡有「不通過就…」「改到…為止」「最多 N 輪」而且計畫裡有審查步驟時，程式自動加上。
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field

from village.core.context import PREV
from village.llm.base import ChatMessage, LLMError

log = logging.getLogger("village.loop")

MAX_ROUNDS = 5  # 村規「迴圈最多幾輪」的上限
MAX_TOTAL_STEPS = 15  # 每次問答實際執行的步驟總數上限（含重做）

VERDICT_SUFFIX = (
    "\n\n（審查格式：先列出要修改的地方（具體指出哪裡、為什麼、怎麼改），"
    "最後一行只寫「結論：通過」或「結論：不通過」。沒有需要修改的地方就寫「結論：通過」。）"
)

_VERDICT = re.compile(r"結論\s*[:：]?\s*[*【「]*\s*(不通過|未通過|沒通過|通過)")
_LOOP_WORDS = re.compile(
    r"不通過|沒通過|未通過|改到|直到.{0,12}(通過|過關|滿意|沒問題|為止)|最多\s*[\d一二兩三四五]+\s*(輪|次|遍|回)"
    r"|退回|重來|重寫|重做|再改"
)
_REDO_ALL = re.compile(r"補資料|補充資料|重查|重新查|重新搜尋|再查|補查|補來源|重新研究")
_CHECKER = re.compile(r"檢查|審|校對|挑|評|核對|把關")
_ROUNDS = re.compile(r"(?:最多|至多|上限)\s*([\d一二兩三四五]+)\s*(?:輪|次|遍|回)")
_CN = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5}


@dataclass
class Loop:
    check: str  # 審查步驟 id
    back_to: str  # 不通過時退回的步驟 id（從這步重做到審查步驟）
    max_rounds: int = 0  # 0 = 用村規的上限

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Verdict:
    passed: bool
    feedback: str  # 要修改的地方（通過時可能是空的）
    how: str  # "結論" / "LLM 判斷" / "判斷不出來，當作通過"

    def to_dict(self) -> dict:
        return asdict(self)


def rounds_in(text: str) -> int:
    """請求裡寫的「最多 N 輪」；沒寫回 0。"""
    m = _ROUNDS.search(text)
    if not m:
        return 0
    n = m.group(1)
    return int(n) if n.isdigit() else _CN.get(n, 0)


def validate(loop: Loop | None, steps: list) -> Loop | None:
    """檢查迴圈參照的步驟：審查步驟要存在、要用到前面的步驟；退回的步驟要在審查步驟之前。"""
    if loop is None:
        return None
    ids = [s.id for s in steps]
    if loop.check not in ids or loop.back_to not in ids or ids.index(loop.back_to) >= ids.index(loop.check):
        return None
    check = steps[ids.index(loop.check)]
    checked = [u for u in check.use if u != PREV]
    if not checked:
        return None
    by_id = {s.id: s for s in steps}
    if any(by_id[u].agent == check.agent for u in checked if u in by_id):
        return None  # 自己審自己：退回也是同一位，沒有意義（多半是該寫的村民停用了，村長只好派給審查者）
    return Loop(loop.check, loop.back_to, max(0, min(MAX_ROUNDS, int(loop.max_rounds or 0))))


def from_message(steps: list, message: str) -> Loop | None:
    """不靠 LLM：請求像在要求「不通過就重做」，而且計畫裡有審查步驟 → 加上迴圈。"""
    if not _LOOP_WORDS.search(message):
        return None
    ids = [s.id for s in steps]
    for s in reversed(steps):
        uses = [u for u in s.use if u != PREV and u in ids]
        if uses and (s.agent == "editor" or _CHECKER.search(s.instruction)):
            back = steps[0].id if _REDO_ALL.search(message) else max(uses, key=ids.index)
            return Loop(s.id, back, rounds_in(message))
    return None


def parse_verdict(text: str) -> Verdict | None:
    """讀審查意見最後的「結論：通過 / 不通過」（取最後一個，避免意見裡引用到的字）。"""
    found = _VERDICT.findall(text)
    if not found:
        return None
    passed = found[-1] == "通過"
    feedback = _VERDICT.sub("", text).strip().rstrip("（）()").strip()
    return Verdict(passed, "" if passed else feedback, "結論")


JUDGE_SYSTEM = (
    "你負責判讀一段審查意見的結論。審查意見如果表示內容可以接受（沒有問題、只有可忽略的小建議），passed 為 true；"
    "如果要求修改，passed 為 false，feedback 用一兩句話整理要修改的重點。只輸出 JSON。"
)
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"passed": {"type": "boolean"}, "feedback": {"type": "string"}},
    "required": ["passed", "feedback"],
    "additionalProperties": False,
}


async def judge(llm, text: str) -> Verdict:
    """沒有寫結論（例如駐村專家不照格式）：請小模型判；也判不出來就當作通過（不會無限重做）。"""
    if v := parse_verdict(text):
        return v
    try:
        r = await llm.chat("tier:small", [ChatMessage("user", f"審查意見：\n{text[:3000]}")],
                           system=JUDGE_SYSTEM, json_schema=JUDGE_SCHEMA, max_tokens=300)
        raw = r.text.strip()
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start : end + 1])
        passed = bool(data["passed"])
        return Verdict(passed, "" if passed else (str(data.get("feedback") or "").strip() or text.strip()), "LLM 判斷")
    except (LLMError, ValueError, KeyError, TypeError) as e:
        log.warning("判讀審查結論失敗：%s", e)
        return Verdict(True, "", "判斷不出來，當作通過")


@dataclass
class LoopState:
    """這次問答的迴圈進度（Runner 是全村共用的，放 context variable）。"""

    loop: Loop | None = None
    round: int = 1
    max_rounds: int = 1
    span: list[str] = field(default_factory=list)  # 重做的範圍（back_to … check）
    feedback: str = ""  # 上一輪的審查意見
    checker: str = ""  # 審查者的名字
    previous: dict[str, str] = field(default_factory=dict)  # 上一輪各步驟的成果

    def note_for(self, step_id: str) -> str:
        """重做時給這一步的額外參考：上一版成果＋審查意見。"""
        if self.round <= 1 or step_id not in self.span or not self.feedback:
            return ""
        parts = [f"審查意見（第 {self.round - 1} 輪，來自{self.checker}，這一輪要針對這些地方修改）：\n{self.feedback}"]
        if (prev := self.previous.get(step_id)) and self.loop and step_id != self.loop.check:
            parts.append(f"你上一版的成果（請改進它，不要整個忽略）：\n{prev}")
        if self.loop and step_id == self.loop.check:
            parts.append("這是修改後的版本：確認上一輪指出的問題有沒有改好。")
        return "\n\n".join(parts)
