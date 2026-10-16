"""日期：今天幾號、距離某天還有幾天、兩個日期相差幾天、某天加減幾天（台灣時間）。

設計重點：LLM 不知道今天的日期，所以「是哪一年」一律由程式決定，不交給 LLM。
  - 日期可以不寫年份（05-20、5月20日）或寫節日名稱（跨年、聖誕節…）
  - until（還有幾天）自動抓「下一次」：今年已經過了就算明年
  - 每次呼叫都把今天的日期附在說明裡（context()），LLM 不會去猜
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from village.tools.base import Tool, ToolError, ToolResult

TZ = timezone(timedelta(hours=8))  # 台灣時間（容器不一定有時區資料庫，用固定 +8）
WEEK = "一二三四五六日"

# 固定日期的節日（農曆節日每年不同，不放在這裡）
NAMED = {
    "跨年": (12, 31), "跨年夜": (12, 31), "除夕夜": None, "元旦": (1, 1), "新年": (1, 1),
    "情人節": (2, 14), "白色情人節": (3, 14), "婦女節": (3, 8), "愚人節": (4, 1), "勞動節": (5, 1),
    "教師節": (9, 28), "國慶": (10, 10), "國慶日": (10, 10), "雙十節": (10, 10), "萬聖節": (10, 31),
    "光棍節": (11, 11), "雙十一": (11, 11), "聖誕節": (12, 25), "聖誕夜": (12, 24), "平安夜": (12, 24),
}


def today() -> date:
    return datetime.now(TZ).date()


def _d(d: date) -> str:
    return f"{d.year} 年 {d.month} 月 {d.day} 日（星期{WEEK[d.weekday()]}）"


def parse_date(s: str, field: str) -> tuple[date | None, tuple[int, int] | None]:
    """回傳 (完整日期, None) 或 (None, (月, 日))；年份沒寫時交給呼叫端依操作決定是哪一年。空字串 = 今天。"""
    s = (s or "").strip().replace(" ", "")
    if s in ("", "today", "今天", "今日"):
        return today(), None
    if s in NAMED:
        md = NAMED[s]
        if md is None:
            raise ToolError(f"「{s}」是農曆節日，每年日期不同，請直接寫日期（YYYY-MM-DD）")
        return None, md
    if s in ("明天", "後天", "昨天", "前天"):
        return today() + timedelta(days={"明天": 1, "後天": 2, "昨天": -1, "前天": -2}[s]), None
    m = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})[日號]?$", s)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), None
        m = re.match(r"^(\d{1,2})[-/.月](\d{1,2})[日號]?$", s)
        if m:
            md = (int(m.group(1)), int(m.group(2)))
            date(2024, *md)  # 閏年也能過的檢查（2/29）
            return None, md
    except ValueError:
        raise ToolError(f"{field}「{s}」不是存在的日期") from None
    raise ToolError(f"{field}「{s}」看不懂：請寫 YYYY-MM-DD、MM-DD（沒提到年份）、節日名稱（跨年、聖誕節…），今天就留空")


def _in_year(md: tuple[int, int], year: int) -> date:
    month, day = md
    if (month, day) == (2, 29):
        while True:  # 2/29 只在閏年
            try:
                return date(year, 2, 29)
            except ValueError:
                year += 1
    return date(year, month, day)


def next_occurrence(md: tuple[int, int], base: date) -> date:
    """下一次（含今天）：今年的還沒過就是今年，過了就是明年。"""
    d = _in_year(md, base.year)
    return d if d >= base else _in_year(md, base.year + 1)


class Clock(Tool):
    id = "clock"
    name = "日期"
    description = "今天的日期與星期、距離某天還有幾天（自動抓下一次，例如生日、跨年）、兩個日期相差幾天、某天往前或往後幾天是哪天（台灣時間）。年份由程式判斷，LLM 不用知道今天幾號。"
    keywords = ["今天", "幾號", "星期幾", "禮拜幾", "還有幾天", "剩幾天", "幾天後", "相差幾天", "日期", "倒數"]
    examples = ["今天幾號？", "距離跨年還有幾天", "我的生日是 5 月 20 日，還有幾天", "100 天後是幾月幾號、星期幾"]
    default_prompt = """你是村子裡的報時員。把使用者的問題整理成日期計算，交給程式處理。
operation 怎麼選：
- today：問今天幾號、星期幾。
- until：問「距離某天還有幾天 / 還剩幾天 / 倒數」。date2 填那一天；程式會自動抓「下一次」（今年已經過了就算明年）。
- between：問兩個日期「相差 / 間隔幾天」。date1、date2 分別填起訖日期。
- add：問某天往後 / 往前幾天是哪天。date1 填起始日（今天就留空），days 填天數（往前用負數）。
日期怎麼寫：
- 使用者沒說年份就「不要寫年份」，寫成 MM-DD（例如 05-20），由程式決定是哪一年。
- 有說年份才寫 YYYY-MM-DD。今天就留空字串。
- 節日直接寫名稱：跨年、元旦、聖誕節、情人節、國慶日、萬聖節…（程式認得）。
- 沒用到的欄位：日期填空字串、days 填 0。
範例：
- 「今天星期幾」→ {"operation":"today","date1":"","date2":"","days":0}
- 「距離跨年還有幾天」→ {"operation":"until","date1":"","date2":"跨年","days":0}
- 「我的生日是 5 月 20 日，還剩幾天」→ {"operation":"until","date1":"","date2":"05-20","days":0}
- 「3 月 1 日到 3 月 15 日差幾天」→ {"operation":"between","date1":"03-01","date2":"03-15","days":0}
- 「100 天後是哪天」→ {"operation":"add","date1":"","date2":"","days":100}"""

    def context(self) -> str:
        return f"（參考：今天是 {today().isoformat()}，{_d(today())}。不要因此替使用者補上年份。）"

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["today", "until", "between", "add"]},
                "date1": {"type": "string"},
                "date2": {"type": "string"},
                "days": {"type": "integer"},
            },
            "required": ["operation", "date1", "date2", "days"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, Any]) -> ToolResult:
        op = args.get("operation")
        now = today()
        if op == "diff":  # 舊名稱相容
            op = "between"
        if op == "today":
            text = f"今天是 {_d(now)}"
        elif op == "until":
            full, md = parse_date(args.get("date2") or args.get("date1") or "", "date2")
            target = full if full else next_occurrence(md, now)  # type: ignore[arg-type]
            n = (target - now).days
            label = str(args.get("date2") or "").strip()
            name = f"（{label}）" if label in NAMED else ""
            if n == 0:
                text = f"就是今天！{_d(target)}{name}"
            elif n > 0:
                text = f"距離 {_d(target)}{name}還有 {n} 天"
            else:  # 使用者明確寫了過去的年份
                text = f"{_d(target)}{name}已經過了 {-n} 天"
        elif op == "between":
            f1, md1 = parse_date(args.get("date1", ""), "date1")
            f2, md2 = parse_date(args.get("date2", ""), "date2")
            # 沒寫年份：跟著另一個日期的年份，兩個都沒寫就用今年
            y = (f1 or f2 or now).year
            a = f1 or _in_year(md1, y)  # type: ignore[arg-type]
            b = f2 or _in_year(md2, y)  # type: ignore[arg-type]
            if md2 and not f2 and b < a:  # 例如 12-20 到 01-05：跨年了
                b = _in_year(md2, y + 1)
            n = (b - a).days
            text = f"{_d(a)} 到 {_d(b)} 相差 {abs(n)} 天"
        elif op == "add":
            try:
                days = int(args.get("days") or 0)
            except (TypeError, ValueError):
                raise ToolError("days 要是整數") from None
            if abs(days) > 100_000:
                raise ToolError("天數太大（上限 100000 天）")
            full, md = parse_date(args.get("date1", ""), "date1")
            a = full or next_occurrence(md, now)  # type: ignore[arg-type]
            b = a + timedelta(days=days)
            text = f"{_d(a)} {'往後' if days >= 0 else '往前'} {abs(days)} 天是 {_d(b)}"
        else:
            raise ToolError("operation 只能是 today、until、between、add")
        return ToolResult(text=text, detail=text)

    # ---------------------------------------------------------------- 退路：不靠 LLM 從文字判斷

    _DATE = re.compile(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}[日號]?|\d{1,2}\s*[/月]\s*\d{1,2}\s*[日號]?")

    def fallback(self, instruction: str, context: str) -> dict[str, Any] | None:
        text = instruction
        dates = [d.replace(" ", "") for d in self._DATE.findall(text)]
        names = [n for n in NAMED if n in text and NAMED[n]]
        names = [n for n in names if not any(n != m and n in m for m in names)]  # 「跨年夜」優先於「跨年」
        if m := re.search(r"(-?\d+)\s*天(後|之後|以後|前|之前|以前)", text):
            n = int(m.group(1)) * (-1 if "前" in m.group(2) else 1)
            return {"operation": "add", "date1": dates[0] if dates else "", "date2": "", "days": n}
        if len(dates) >= 2:
            return {"operation": "between", "date1": dates[0], "date2": dates[1], "days": 0}
        if dates or names:
            return {"operation": "until", "date1": "", "date2": dates[0] if dates else names[0], "days": 0}
        if re.search(r"今天|幾號|星期幾|禮拜幾|日期", text):
            return {"operation": "today", "date1": "", "date2": "", "days": 0}
        return None
