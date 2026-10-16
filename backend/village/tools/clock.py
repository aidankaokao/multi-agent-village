"""日期：今天幾號、星期幾、兩個日期差幾天、某天加減幾天。LLM 不知道今天日期，這種問題一定要交給程式。"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from village.tools.base import Tool, ToolError, ToolResult

TZ = timezone(timedelta(hours=8))  # 台灣時間（容器不一定有時區資料庫，用固定 +8）
WEEK = "一二三四五六日"


def today() -> date:
    return datetime.now(TZ).date()


def _d(d: date) -> str:
    return f"{d.year} 年 {d.month} 月 {d.day} 日（星期{WEEK[d.weekday()]}）"


def parse_date(s: str, field: str) -> date:
    s = (s or "").strip()
    if s in ("", "today", "今天"):
        return today()
    m = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$", s)
    if not m:
        raise ToolError(f"{field}「{s}」看不懂，請寫成 YYYY-MM-DD，例如 2026-12-25；今天就留空")
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise ToolError(f"{field}「{s}」不是存在的日期") from None


class Clock(Tool):
    id = "clock"
    name = "日期"
    description = "今天的日期與星期、兩個日期相差幾天、某天往前或往後幾天是哪天（台灣時間）。LLM 不知道今天是幾號，這類問題交給程式最準。"
    keywords = ["今天", "幾號", "星期幾", "禮拜幾", "幾天", "日期", "倒數"]
    examples = ["今天幾號？", "距離 2026-12-25 還有幾天", "100 天後是幾月幾號、星期幾"]
    default_prompt = """你是村子裡的報時員。把使用者的問題整理成日期計算，交給程式處理。
規則：
- operation：問今天日期或星期用 today；問兩個日期差幾天用 diff；問某天往前 / 往後幾天用 add。
- date1、date2 寫成 YYYY-MM-DD；「今天」或沒提到就留空字串。
- days：add 時要加的天數（往前就用負數），其他情況填 0。
- 你不知道今天是幾號，不要自己猜，留空交給程式。
範例：
- 「今天星期幾」→ {"operation":"today","date1":"","date2":"","days":0}
- 「距離 2026-12-25 還有幾天」→ {"operation":"diff","date1":"","date2":"2026-12-25","days":0}
- 「100 天後是哪天」→ {"operation":"add","date1":"","date2":"","days":100}"""

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["today", "diff", "add"]},
                "date1": {"type": "string"},
                "date2": {"type": "string"},
                "days": {"type": "integer"},
            },
            "required": ["operation", "date1", "date2", "days"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, Any]) -> ToolResult:
        op = args.get("operation")
        if op == "today":
            d = today()
            text = f"今天是 {_d(d)}"
        elif op == "diff":
            a, b = parse_date(args.get("date1", ""), "date1"), parse_date(args.get("date2", ""), "date2")
            n = (b - a).days
            text = f"{_d(a)} 到 {_d(b)}：{'相差' if n >= 0 else '已經過了'} {abs(n)} 天"
        elif op == "add":
            try:
                days = int(args.get("days") or 0)
            except (TypeError, ValueError):
                raise ToolError("days 要是整數") from None
            if abs(days) > 100_000:
                raise ToolError("天數太大（上限 100000 天）")
            a = parse_date(args.get("date1", ""), "date1")
            b = a + timedelta(days=days)
            text = f"{_d(a)} {'往後' if days >= 0 else '往前'} {abs(days)} 天是 {_d(b)}"
        else:
            raise ToolError("operation 只能是 today、diff、add")
        return ToolResult(text=text, detail=text)

    def fallback(self, instruction: str, context: str) -> dict[str, Any] | None:
        dates = re.findall(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", instruction)
        if m := re.search(r"(-?\d+)\s*天(後|之後|以後|前|之前|以前)", instruction):
            n = int(m.group(1)) * (-1 if "前" in m.group(2) else 1)
            return {"operation": "add", "date1": dates[0] if dates else "", "date2": "", "days": n}
        if dates:
            return {"operation": "diff", "date1": dates[1] if len(dates) > 1 else "", "date2": dates[0], "days": 0} if len(dates) == 1 else {"operation": "diff", "date1": dates[0], "date2": dates[1], "days": 0}
        if re.search(r"今天|幾號|星期幾|禮拜幾|日期", instruction):
            return {"operation": "today", "date1": "", "date2": "", "days": 0}
        return None
