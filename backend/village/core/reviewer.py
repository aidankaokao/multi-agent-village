"""審核員：最終答案送出前，用 LLM 檢查有沒有回答到使用者的問題、是否符合要求、內容是否恰當。

和村長、工匠一樣是系統角色（id: reviewer）。為了不讓弱模型亂退件：
  - 只有「明顯」問題才判不合格，風格偏好、有疑慮時一律通過
  - 結構化輸出 {pass, reason, fix, step}；解析失敗或 LLM 不能用時略過審核（不擋住答案）
"""

import logging
import time
from dataclasses import asdict, dataclass

from village.core.orchestrator import ChatLLM, PlanError, _extract_json
from village.llm.base import ChatMessage, LLMError

log = logging.getLogger("village.reviewer")

REVIEWER_ID = "reviewer"
MAX_ANSWER_CHARS = 6000  # 太長的答案只給審核員看前段，省 token


@dataclass
class Leaf:
    """被審核的成果：最後交給使用者的那幾步。"""

    step_id: str
    agent_name: str
    instruction: str
    output: str


@dataclass
class Review:
    passed: bool
    reason: str
    fix: str = ""
    step: str | None = None  # 要重做的步驟
    model: str | None = None
    elapsed_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def review_schema(step_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "pass": {"type": "boolean"},
            "reason": {"type": "string"},
            "fix": {"type": "string"},
            "step": {"type": "string", "enum": step_ids},
        },
        "required": ["pass", "reason", "fix", "step"],
        "additionalProperties": False,
    }


SYSTEM = """你是村子裡的審核員，在答案交給使用者之前做最後檢查。

只檢查這三件事：
1. 有沒有做到使用者要求的事（主題對、要求的每個動作都有完成，例如要翻譯就要有譯文、要改成笑話就要是笑話）
2. 有沒有違反使用者「明確」提出的要求（指定的語言、形式、數量、長度）
3. 內容是否恰當（沒有髒話、歧視、危險或違法的內容）

判定原則：
- 只有「明顯」的問題才判 pass=false。文筆、風格、你個人的偏好都不算問題；有疑慮時判 pass=true。
- pass=true 時：reason 用一句話說明為什麼合格，fix 留空字串。
- pass=false 時：reason 用一句話說明哪裡不合格；fix 用一句話告訴負責的村民具體要怎麼改。
- step：填有問題那一步的 id（合格時填第一個 id 即可）。
用繁體中文。只輸出 JSON：{"pass": true 或 false, "reason": "...", "fix": "...", "step": "..."}"""


def render(message: str, leaves: list[Leaf]) -> str:
    parts = [f"使用者的請求：{message}", ""]
    for lf in leaves:
        out = lf.output if len(lf.output) <= MAX_ANSWER_CHARS else lf.output[:MAX_ANSWER_CHARS] + "\n…（後略）"
        parts += [f"【{lf.step_id}｜{lf.agent_name}】任務：{lf.instruction}", "成果：", out, ""]
    return "\n".join(parts).strip()


async def review(llm: ChatLLM, message: str, leaves: list[Leaf], target: str = "tier:large") -> Review | None:
    """回傳 None 代表沒辦法審核（LLM 不能用或輸出無法解析），呼叫端應直接放行。"""
    ids = [lf.step_id for lf in leaves]
    t0 = time.monotonic()
    try:
        r = await llm.chat(
            target, [ChatMessage("user", render(message, leaves))], system=SYSTEM,
            json_schema=review_schema(ids), max_tokens=1024, fallback="tier:large",
        )
        data = _extract_json(r.text)
    except (LLMError, PlanError) as e:
        log.warning("審核員無法審核，略過：%s", e)
        return None
    passed = data.get("pass")
    if not isinstance(passed, bool):
        return None
    step = data.get("step") if data.get("step") in ids else ids[-1]
    return Review(
        passed=passed,
        reason=str(data.get("reason") or "").strip() or ("合格" if passed else "不合格"),
        fix="" if passed else str(data.get("fix") or "").strip(),
        step=step,
        model=r.model,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        tokens_in=r.usage.get("input_tokens", 0),
        tokens_out=r.usage.get("output_tokens", 0),
    )
