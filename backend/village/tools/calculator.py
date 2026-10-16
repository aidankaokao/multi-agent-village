"""計算機：安全的算式求值（AST 白名單，不用 eval），LLM 只負責把自然語言轉成算式。"""

import ast
import math
import operator
import re
from typing import Any

from village.tools.base import Tool, ToolError, ToolResult

MAX_EXPR = 300
MAX_POW = 10_000  # 指數上限，避免 9**9**9 這種炸掉 CPU 的算式
MAX_ABS = 1e300

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _factorial(n: float) -> int:
    if n != int(n) or n < 0 or n > 170:
        raise ToolError("factorial 只接受 0～170 的整數")
    return math.factorial(int(n))


FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max,
    "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "floor": math.floor, "ceil": math.ceil,
    "factorial": _factorial,
}
CONSTS = {"pi": math.pi, "e": math.e}

# 全形與常見符號 → 半形運算子
_TRANS = str.maketrans({
    "×": "*", "✕": "*", "＊": "*", "÷": "/", "／": "/", "＋": "+", "－": "-", "−": "-", "（": "(", "）": ")",
    "＾": "^", "％": "%", "，": ",", "．": ".", **{chr(0xFF10 + i): str(i) for i in range(10)},
})


def normalize(expr: str) -> str:
    s = expr.translate(_TRANS).strip().rstrip("=").strip()
    s = s.replace("^", "**")
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)  # 千分位逗號 1,234 → 1234
    s = re.sub(r"(\d+(?:\.\d+)?)%(?!\s*[\d(])", r"(\1/100)", s)  # 15% → (15/100)；15 % 4 仍是取餘數
    return s


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in CONSTS:
        return CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > MAX_POW or (abs(a) > 1e6 and abs(b) > 100)):
            raise ToolError("指數太大")
        try:
            r = _BIN[type(node.op)](a, b)
        except ZeroDivisionError:
            raise ToolError("不能除以 0") from None
        if isinstance(r, complex):
            raise ToolError("結果是複數（例如負數開根號），無法計算")
        return r
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS and not node.keywords:
        args = [_eval(a) for a in node.args]
        try:
            return FUNCS[node.func.id](*args)
        except (ValueError, TypeError) as e:
            raise ToolError(f"{node.func.id} 的參數不對：{e}") from None
    if isinstance(node, ast.Name):
        raise ToolError(f"不認得「{node.id}」，只能用數字、運算子與函數：{', '.join(FUNCS)}、pi、e")
    raise ToolError(f"不支援的語法：{type(node).__name__}")


def evaluate(expr: str) -> float | int:
    s = normalize(expr)
    if not s:
        raise ToolError("算式是空的")
    if len(s) > MAX_EXPR:
        raise ToolError(f"算式太長（上限 {MAX_EXPR} 字元）")
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError:
        raise ToolError(f"算式格式錯誤：{s}") from None
    r = _eval(tree)
    if isinstance(r, float) and (math.isnan(r) or math.isinf(r) or abs(r) > MAX_ABS):
        raise ToolError("結果太大或不是數字")
    return r


def fmt(x: float | int) -> str:
    """整數不帶小數點、浮點數最多 10 位小數並去掉尾端 0，加千分位。"""
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        x = int(x)
    if isinstance(x, int):
        return f"{x:,}"
    s = f"{x:,.10f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


# 退路：把中文描述換成運算子，再抓出最長的一段算式
_WORDS = [
    ("乘以", "*"), ("乘上", "*"), ("乘", "*"), ("除以", "/"), ("除", "/"),
    ("加上", "+"), ("加", "+"), ("減去", "-"), ("減掉", "-"), ("減", "-"), ("的平方", "**2"),
]
_EXPR_RUN = re.compile(r"[0-9.\s+\-*/%()^×÷（）＋－＊／]+")


def extract_expression(text: str) -> str | None:
    s = text.translate(_TRANS)
    s = re.sub(r"的\s*(\d+)\s*次方", r"**\1", s)
    for w, op in _WORDS:
        s = s.replace(w, op)
    best = ""
    for m in _EXPR_RUN.finditer(s):
        part = m.group(0).strip()
        if re.search(r"\d", part) and re.search(r"\d\s*[-+*/%^]+\s*[\d(]", part) and len(part) > len(best):
            best = part
    return best or None


class Calculator(Tool):
    id = "calculator"
    name = "計算機"
    description = "精準計算：四則運算、百分比、次方、開根號、三角函數等。LLM 只負責把問題轉成算式，計算由程式完成，結果一定正確。"
    keywords = ["計算", "算一下", "多少錢", "等於多少", "加起來", "平均", "百分之", "打折", "乘以", "除以"]
    examples = ["123 乘以 456 是多少", "一杯 65 元，買 7 杯打 9 折要多少錢", "2 的 20 次方", "sqrt(2) 等於多少"]
    default_prompt = """你是村子裡的計算機。把使用者的問題轉成「一個」數學算式，交給程式計算。
規則：
- 只能用數字、+ - * / ** % ( ) 與函數 sqrt abs round min max log log10 log2 exp sin cos tan floor ceil factorial，常數 pi e。
- 百分比寫成小數，例如打 9 折寫 *0.9、加 5% 稅寫 *1.05。
- 不要自己算答案，只輸出算式；不要帶單位或文字。
範例：
- 「一杯 65 元，買 7 杯打 9 折」→ 65*7*0.9
- 「2 的 20 次方」→ 2**20
- 「3、5、10 的平均」→ (3+5+10)/3"""

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, Any]) -> ToolResult:
        expr = str(args.get("expression") or "").strip()
        value = evaluate(expr)
        shown = normalize(expr).replace("**", "^")
        line = f"{shown} = {fmt(value)}"
        return ToolResult(text=line, detail=line)

    def fallback(self, instruction: str, context: str) -> dict[str, Any] | None:
        expr = extract_expression(instruction) or extract_expression(context)
        return {"expression": expr} if expr else None
