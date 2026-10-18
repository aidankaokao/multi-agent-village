"""報告圖表：數字全由程式算（分組小計、按月彙總），畫成 SVG 嵌進 PDF；LLM 只從候選清單挑要放哪幾張。

候選圖表：
- 依類別（分店、部門、品項…）的分組 → 橫條圖（中文類別名放左邊比較好讀）
- 依時間（日期欄按月彙總；或文字欄的值像「2025-01」「1月」「Q1」）→ 折線圖
每張圖畫一個數字欄：單價、比率、分數這類欄位畫「平均」（合計沒有意義），其他畫「合計」。
"""

import re
from dataclasses import dataclass
from html import escape

from app.readers import Document

MAX_CHARTS = 4
MAX_CANDIDATES = 24

# 欄名像這些的，畫平均（合計沒有意義）
_MEAN_HINT = re.compile(r"價|率|%|％|比|分數|評分|NPS|平均|單價|溫度|年齡|score|rate|ratio|avg|mean|price", re.I)
# 不是「量」的數字欄：年份、編號、電話…（加總、平均都沒有意義，不畫）
_NOT_MEASURE = re.compile(r"年$|年份|年度$|^年|編號|代碼|代號|序號|號碼|電話|郵遞|統編|id$|^no\.?$|year|code|phone|zip", re.I)
_PERIOD = re.compile(r"^(\d{4}[-/.年]\d{1,2}月?|\d{1,2}月|\d{4}年?|(Q|第)\s?[1-4]\s?季?|\d{4}\s?Q[1-4])$", re.I)


def prefers_mean(column: str) -> bool:
    return bool(_MEAN_HINT.search(column))


def is_measure(column) -> bool:
    """數字欄能不能拿來畫圖（column 是 readers.Column）。"""
    if _NOT_MEASURE.search(column.name.strip()):
        return False
    n = column.numeric or {}
    looks_like_year = isinstance(n.get("min"), int) and isinstance(n.get("max"), int) and 1900 <= n["min"] <= n["max"] <= 2100
    return not looks_like_year


def _is_period(keys: list[str]) -> bool:
    return len(keys) >= 2 and all(_PERIOD.match(k.strip()) for k in keys)


def _period_order(key: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", key)) or (0,)


@dataclass
class Candidate:
    id: str  # c1、c2…（給 LLM 挑）
    doc: str
    table: str
    by: str  # 分組的欄（分店 / 月份 / 日期）
    column: str  # 畫哪個數字欄
    metric: str  # sum / mean
    kind: str  # bar（類別）/ line（時間）
    points: list[tuple[str, float]]

    @property
    def title(self) -> str:
        what = f"{self.column}{'平均' if self.metric == 'mean' else '合計'}"
        return f"每月{what}" if self.kind == "line" and self.by_month else f"依{self.by}的{what}"

    @property
    def by_month(self) -> bool:
        return self.kind == "line" and all(re.match(r"^\d{4}-\d{2}$", k) for k, _ in self.points)

    def describe(self) -> str:
        shape = "折線圖" if self.kind == "line" else "橫條圖"
        return f"[{self.id}] {self.title}（{shape}，{len(self.points)} 個點；出自 {self.doc}／{self.table}）"

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "kind": self.kind, "source": f"{self.doc}／{self.table}"}


def candidates(docs: list[Document]) -> list[Candidate]:
    out: list[Candidate] = []
    for d in docs:
        for t in d.tables:
            measures = {c.name for c in t.columns if c.numeric and is_measure(c)}
            for g, from_dates in [(g, False) for g in t.groups] + [(g, True) for g in t.series]:
                keys = [x["key"] for x in g["groups"]]
                line = from_dates or _is_period(keys)
                rows = sorted(g["groups"], key=lambda x: _period_order(x["key"])) if line and not from_dates else g["groups"]
                for j, col in enumerate(g["sums"]):
                    if col not in measures:
                        continue
                    metric = "mean" if prefers_mean(col) else "sum"
                    pts = [(x["key"], float(x["means" if metric == "mean" else "sums"][j])) for x in rows]
                    if not line:
                        pts.sort(key=lambda p: -p[1])  # 橫條圖由大到小
                    out.append(Candidate("", d.name, t.name, g["by"], col, metric, "line" if line else "bar", pts))
    for i, c in enumerate(out[:MAX_CANDIDATES], 1):
        c.id = f"c{i}"
    return out[:MAX_CANDIDATES]


def mentioned(cands: list[Candidate], question: str) -> list[Candidate]:
    """要求裡直接提到的欄位（例如「著重人事成本與 NPS」）：每個欄位一張，類別比較優先（看得出誰高誰低）。"""
    q = question.lower()
    out: list[Candidate] = []
    cols: set[str] = set()
    for c in sorted(cands, key=lambda c: c.kind != "bar"):
        if c.column not in cols and len(c.column) >= 2 and c.column.lower() in q:
            cols.add(c.column)
            out.append(c)
    return out


def merge(*groups: list[Candidate]) -> list[Candidate]:
    out: list[Candidate] = []
    for g in groups:
        for c in g:
            if c not in out:
                out.append(c)
    return out[:MAX_CHARTS]


def default_picks(cands: list[Candidate], n: int = 3) -> list[Candidate]:
    """沒有 LLM（或 LLM 沒挑）時：每種分組先挑第一個「合計」的欄（通常是金額 / 數量），時間趨勢優先。"""
    picked: list[Candidate] = []
    seen: set[tuple] = set()
    for c in sorted(cands, key=lambda c: (c.kind != "line", c.metric != "sum")):
        key = (c.doc, c.table, c.by)
        if key in seen:
            continue
        seen.add(key)
        picked.append(c)
        if len(picked) >= n:
            break
    return picked


# ---------------------------------------------------------------- 畫 SVG

COLOR = "#457b9d"
FONT = "Noto Sans CJK TC, Noto Sans TC, PingFang TC, Microsoft JhengHei, sans-serif"


def short_number(x: float) -> str:
    """1,175.4萬、3.7億；小數字保留到兩位。"""
    a = abs(x)
    if a >= 1e8:
        s = f"{x / 1e8:,.2f}".rstrip("0").rstrip(".") + "億"
    elif a >= 1e4:
        s = f"{x / 1e4:,.1f}".rstrip("0").rstrip(".") + "萬"
    elif float(x).is_integer():
        s = f"{int(x):,}"
    else:
        s = f"{x:,.2f}".rstrip("0").rstrip(".")
    return s


def _label(text: str, n: int = 10) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _text(x, y, s, anchor="start", size=10, color="#333", weight="normal") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" fill="{color}" '
            f'font-weight="{weight}" font-family="{FONT}">{escape(s)}</text>')


def bar_svg(points: list[tuple[str, float]], width: int = 480) -> str:
    left, right, row = 96, 64, 22
    height = row * len(points) + 12
    lo = min(0.0, min(v for _, v in points))
    hi = max(0.0, max(v for _, v in points)) or 1.0
    span = (hi - lo) or 1.0
    plot = width - left - right
    zero = left + (0 - lo) / span * plot
    parts = [f'<line x1="{zero:.1f}" y1="4" x2="{zero:.1f}" y2="{height - 6}" stroke="#bbb" stroke-width="1"/>']
    for i, (k, v) in enumerate(points):
        y = 6 + i * row
        x0, x1 = sorted((zero, left + (v - lo) / span * plot))
        parts.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{max(1.0, x1 - x0):.1f}" height="{row - 8}" fill="{COLOR}" rx="2"/>')
        parts.append(_text(left - 6, y + row - 11, _label(k), "end"))
        parts.append(_text(x1 + 4, y + row - 11, short_number(v), size=9, color="#555"))
    return _svg(width, height, parts)


def line_svg(points: list[tuple[str, float]], width: int = 480, height: int = 190) -> str:
    left, right, top, bottom = 60, 34, 16, 34
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.15 or abs(hi) * 0.1 or 1.0
    lo, hi = (0.0 if lo >= 0 and lo - pad < 0 else lo - pad), hi + pad
    plot_w, plot_h = width - left - right, height - top - bottom
    step = plot_w / max(1, len(points) - 1)

    def xy(i: int, v: float) -> tuple[float, float]:
        return left + i * step, top + (hi - v) / (hi - lo) * plot_h

    parts = []
    for j in range(5):  # 橫格線與刻度
        v = lo + (hi - lo) * j / 4
        y = top + plot_h - plot_h * j / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#e5e5e5" stroke-width="1"/>')
        parts.append(_text(left - 4, y + 3, short_number(v), "end", 8, "#888"))
    pts = [xy(i, v) for i, v in enumerate(vals)]
    parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{COLOR}" stroke-width="2"/>')
    every = max(1, -(-len(points) // 12))  # 點太多時隔幾個才標
    for i, ((k, v), (x, y)) in enumerate(zip(points, pts, strict=True)):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.8" fill="{COLOR}"/>')
        if i % every == 0 or i == len(points) - 1:
            parts.append(_text(x, height - bottom + 14, _label(k, 8), "middle", 8, "#555"))
            # 第一個點的數值往右偏，免得壓到左邊的刻度
            parts.append(_text(x + (14 if i == 0 else 0), y - 6, short_number(v), "middle", 8, "#555"))
    return _svg(width, height, parts)


def _svg(w: int, h: int, parts: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            + "".join(parts) + "</svg>")


def render(c: Candidate) -> str:
    return line_svg(c.points) if c.kind == "line" else bar_svg(c.points)
