"""圖表：長條圖、折線圖、圓餅圖，產生 SVG（不需要額外套件）。

LLM 只負責從請求或前一步成果整理出「標籤 + 數值」；畫圖由程式完成。
LLM 不能用時，退路會從文字裡抓「標籤: 數值」或 CSV 表格。
"""

import math
import re
from html import escape
from typing import Any

from village.tools.base import Attachment, Tool, ToolError, ToolResult

W, H = 640, 400
PAD_L, PAD_R, PAD_T, PAD_B = 64, 24, 56, 64
MAX_POINTS, MAX_SERIES = 40, 5
PALETTE = ["#3a6fc4", "#e0565e", "#f2c230", "#5cbf7a", "#9a7af2", "#e08a2c", "#2f9a9a", "#d86aa8"]
FONT = 'font-family="PingFang TC, Noto Sans TC, sans-serif"'
KINDS = {"bar": "長條圖", "line": "折線圖", "pie": "圓餅圖"}


def _num(v: float) -> str:
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M".replace(".0M", "M")
    if abs(v) >= 1e4:
        return f"{v / 1e3:.1f}k".replace(".0k", "k")
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _full(v: float) -> str:
    """完整數字（千分位、去掉多餘小數）：給文字摘要與圖例用，軸上刻度才用縮寫。"""
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def _text(x, y, s, size=12, anchor="middle", color="#333", weight="normal", rotate=None) -> str:
    rot = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{color}" '
        f'font-weight="{weight}" {FONT}{rot}>{escape(str(s))}</text>'
    )


def _legend(series: list[dict], y: float) -> list[str]:
    if len(series) < 2:
        return []
    out, x = [], PAD_L
    for i, s in enumerate(series):
        out.append(f'<rect x="{x}" y="{y - 9}" width="12" height="12" fill="{PALETTE[i % len(PALETTE)]}"/>')
        out.append(_text(x + 16, y + 1, s["name"], 12, "start"))
        x += 28 + 12 * len(s["name"])
    return out


def _axes(labels: list[str], vmin: float, vmax: float, x_label: str, y_label: str) -> list[str]:
    out = []
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    for i in range(6):  # 橫向格線 + 刻度
        v = vmin + (vmax - vmin) * i / 5
        y = PAD_T + ph - ph * i / 5
        out.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" stroke="#ddd"/>')
        out.append(_text(PAD_L - 6, y + 4, _num(v), 11, "end", "#666"))
    out.append(f'<line x1="{PAD_L}" y1="{PAD_T + ph}" x2="{W - PAD_R}" y2="{PAD_T + ph}" stroke="#888"/>')
    step = pw / len(labels)
    tilt = len(labels) > 8 or max(len(lb) for lb in labels) * len(labels) > 50
    for i, lb in enumerate(labels):
        x = PAD_L + step * (i + 0.5)
        y = PAD_T + ph + 16
        out.append(_text(x, y, lb if len(lb) <= 10 else lb[:9] + "…", 11, "end" if tilt else "middle", "#444", rotate=-35 if tilt else None))
    if x_label:
        out.append(_text(PAD_L + pw / 2, H - 8, x_label, 12, color="#555"))
    if y_label:
        out.append(_text(14, PAD_T + ph / 2, y_label, 12, color="#555", rotate=-90))
    return out


def render_svg(kind: str, title: str, labels: list[str], series: list[dict], x_label: str = "", y_label: str = "") -> str:
    body: list[str] = [f'<rect width="{W}" height="{H}" fill="#fffdf6"/>']
    body.append(_text(W / 2, 28, title or KINDS[kind], 18, color="#222", weight="bold"))
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    if kind == "pie":
        values = series[0]["values"]
        total = sum(values)
        cx, cy, r = W * 0.36, PAD_T + ph / 2 + 8, min(pw, ph) / 2
        a0 = -math.pi / 2
        for i, (lb, v) in enumerate(zip(labels, values, strict=True)):
            a1 = a0 + 2 * math.pi * v / total
            color = PALETTE[i % len(PALETTE)]
            if v / total > 0.9999:
                body.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>')
            elif v > 0:
                large = 1 if a1 - a0 > math.pi else 0
                x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
                x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
                body.append(
                    f'<path d="M{cx:.1f},{cy:.1f} L{x0:.1f},{y0:.1f} A{r:.1f},{r:.1f} 0 {large} 1 {x1:.1f},{y1:.1f} Z" '
                    f'fill="{color}" stroke="#fffdf6" stroke-width="2"/>'
                )
            ly = PAD_T + 10 + i * 22  # 圖例：標籤 + 百分比
            body.append(f'<rect x="{W * 0.66:.0f}" y="{ly}" width="12" height="12" fill="{color}"/>')
            body.append(_text(W * 0.66 + 18, ly + 11, f"{lb}  {_full(v)}（{v / total:.0%}）", 12, "start"))
            a0 = a1
        return _wrap(body)

    all_v = [v for s in series for v in s["values"]]
    vmin = min(0.0, min(all_v))
    vmax = _nice_max(max(all_v)) if max(all_v) > 0 else 1
    if vmin < 0:
        vmin = -_nice_max(-vmin)
    body += _axes(labels, vmin, vmax, x_label, y_label)
    scale = ph / (vmax - vmin)
    base_y = PAD_T + ph - (0 - vmin) * scale
    step = pw / len(labels)

    if kind == "bar":
        group = step * 0.75
        bw = group / len(series)
        for si, s in enumerate(series):
            color = PALETTE[si % len(PALETTE)]
            for i, v in enumerate(s["values"]):
                x = PAD_L + step * i + (step - group) / 2 + bw * si
                y = base_y - v * scale
                body.append(f'<rect x="{x:.1f}" y="{min(y, base_y):.1f}" width="{bw - 2:.1f}" height="{abs(base_y - y):.1f}" fill="{color}"/>')
                if len(labels) * len(series) <= 16:
                    body.append(_text(x + (bw - 2) / 2, min(y, base_y) - 4, _num(v), 10, color="#333"))
    else:  # line
        for si, s in enumerate(series):
            color = PALETTE[si % len(PALETTE)]
            pts = [(PAD_L + step * (i + 0.5), base_y - v * scale) for i, v in enumerate(s["values"])]
            body.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{color}" stroke-width="3"/>')
            for (x, y), v in zip(pts, s["values"], strict=True):
                body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
                if len(labels) <= 12 and len(series) == 1:
                    body.append(_text(x, y - 9, _num(v), 10, color="#333"))
    body += _legend(series, 48)
    return _wrap(body)


def _wrap(body: list[str]) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{"".join(body)}</svg>'


def _floats(raw: Any, n: int, name: str) -> list[float]:
    if not isinstance(raw, list) or len(raw) != n:
        raise ToolError(f"「{name}」的 values 要有 {n} 個數字，和 labels 一樣多")
    out = []
    for v in raw:
        try:
            f = float(str(v).replace(",", ""))
        except ValueError:
            raise ToolError(f"「{name}」的 values 裡有不是數字的值：{v}") from None
        if math.isnan(f) or math.isinf(f):
            raise ToolError(f"「{name}」的 values 裡有不是數字的值：{v}")
        out.append(f)
    return out


# 退路：抓「標籤: 數值」或「標籤,數值」一行一筆
_PAIR = re.compile(r"^\s*[-・*•]?\s*([^\d:：,，\t]{1,20}?)\s*[:：,，\t]?\s*(-?[\d,]+(?:\.\d+)?)\s*[^\d]*$")


def extract_table(text: str) -> tuple[list[str], list[float]]:
    labels, values = [], []
    for line in text.splitlines():
        if m := _PAIR.match(line):
            labels.append(m.group(1).strip())
            values.append(float(m.group(2).replace(",", "")))
    return labels, values


class Chart(Tool):
    id = "chart"
    name = "圖表"
    description = "把數字資料畫成長條圖、折線圖或圓餅圖（產生圖片顯示在回答裡）。LLM 只負責整理出標籤和數值，畫圖由程式完成。"
    keywords = ["圖表", "長條圖", "折線圖", "圓餅圖", "畫圖", "畫成圖", "趨勢圖", "chart"]
    examples = [
        "把一月 120、二月 98、三月 150 畫成長條圖",
        "畫一個圓餅圖：房租 15000、餐費 8000、交通 2000",
        "把前面的資料畫成折線圖",
    ]
    default_prompt = """你是村子裡的圖表師。從使用者的請求（或參考資料）整理出要畫的資料，交給程式畫圖。
規則：
- chart：比較大小用 bar、看隨時間變化用 line、看比例組成用 pie；使用者有指定就照他說的。
- labels：每一筆資料的名稱（例如月份、品項），依原本的順序。
- series：一組或多組數值，每組的 values 數量要和 labels 一樣多、只能是數字；name 是這組數值代表什麼。圓餅圖只能有一組。
- title：簡短的圖表標題；x_label、y_label 是座標軸名稱（可留空字串）。
- 只能使用請求或參考資料裡出現的數字，不要編造。
範例：「一月 120、二月 98、三月 150 畫成長條圖」→
{"chart":"bar","title":"每月數量","x_label":"月份","y_label":"","labels":["一月","二月","三月"],"series":[{"name":"數量","values":[120,98,150]}]}"""

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "chart": {"type": "string", "enum": list(KINDS)},
                "title": {"type": "string"},
                "x_label": {"type": "string"},
                "y_label": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "series": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "values": {"type": "array", "items": {"type": "number"}}},
                        "required": ["name", "values"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["chart", "title", "x_label", "y_label", "labels", "series"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, Any]) -> ToolResult:
        kind: str = args["chart"] if args.get("chart") in KINDS else "bar"
        labels = [str(x).strip() or f"#{i + 1}" for i, x in enumerate(args.get("labels") or [])]
        if not labels:
            raise ToolError("labels 是空的：至少要有 1 筆資料")
        if len(labels) > MAX_POINTS:
            raise ToolError(f"資料太多（{len(labels)} 筆），最多 {MAX_POINTS} 筆")
        raw = args.get("series") or []
        if not isinstance(raw, list) or not raw:
            raise ToolError("series 是空的：至少要有一組數值")
        if len(raw) > MAX_SERIES:
            raise ToolError(f"數值組太多（{len(raw)} 組），最多 {MAX_SERIES} 組")
        series = []
        for i, s in enumerate(raw):
            name = str((s or {}).get("name") or f"數列{i + 1}").strip()
            series.append({"name": name, "values": _floats((s or {}).get("values"), len(labels), name)})
        if kind == "pie":
            if len(series) > 1:
                raise ToolError("圓餅圖只能有一組數值；多組請改用 bar 或 line")
            if any(v < 0 for v in series[0]["values"]) or sum(series[0]["values"]) <= 0:
                raise ToolError("圓餅圖的數值要是正數")
        title = str(args.get("title") or "").strip()[:60]
        svg = render_svg(kind, title, labels, series, str(args.get("x_label") or "")[:30], str(args.get("y_label") or "")[:30])
        rows = "\n".join(
            f"- {lb}：" + "、".join(f"{s['name']} {_full(s['values'][i])}" if len(series) > 1 else _full(s["values"][i]) for s in series)
            for i, lb in enumerate(labels)
        )
        head = f"📊 {title or KINDS[kind]}（{KINDS[kind]}，{len(labels)} 筆資料）"
        return ToolResult(
            text=f"{head}\n{rows}",
            attachments=[Attachment(kind="svg", name=f"{title or 'chart'}.svg", data=svg, title=title or KINDS[kind])],
            detail=head,
        )

    def fallback(self, instruction: str, context: str) -> dict[str, Any] | None:
        labels, values = extract_table(context) if context else ([], [])
        if len(labels) < 2:
            labels, values = extract_table(instruction.replace("、", "\n").replace("，", "\n"))
        if len(labels) < 2:
            return None
        kind = "pie" if "圓餅" in instruction else "line" if re.search(r"折線|趨勢|變化", instruction) else "bar"
        return {"chart": kind, "title": "", "x_label": "", "y_label": "", "labels": labels, "series": [{"name": "數值", "values": values}]}
