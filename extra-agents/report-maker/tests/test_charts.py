import io
from datetime import date

from openpyxl import Workbook

from app import charts as ch
from app.config import Limits
from app.readers import read


def book(rows, header) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "資料"
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_candidates_bar_line_and_metrics():
    data = book(
        [["A 店", "2025-01", 100, 150, 2018], ["A 店", "2025-02", 120, 160, 2018],
         ["B 店", "2025-01", 300, 170, 2020], ["B 店", "2025-02", 280, 180, 2020]],
        ["分店", "月份", "營收", "客單價", "開幕年"],
    )
    cands = ch.candidates([read("a.xlsx", data, Limits())])
    titles = [c.title for c in cands]
    assert "依分店的營收合計" in titles and "依分店的客單價平均" in titles  # 單價畫平均
    assert not any("開幕年" in t for t in titles)  # 年份不畫
    bar = next(c for c in cands if c.title == "依分店的營收合計")
    assert bar.kind == "bar" and bar.points == [("B 店", 580.0), ("A 店", 220.0)]  # 由大到小
    line = next(c for c in cands if c.by == "月份" and c.column == "營收")
    assert line.kind == "line" and line.points == [("2025-01", 400.0), ("2025-02", 400.0)]
    assert [c.id for c in cands] == [f"c{i}" for i in range(1, len(cands) + 1)]
    picks = ch.default_picks(cands)
    assert picks[0].kind == "line" and {p.by for p in picks} == {"分店", "月份"}


def test_monthly_series_from_date_column():
    data = book([[date(2025, 1, 5), 10], [date(2025, 1, 20), 5], [date(2025, 3, 1), 7]], ["日期", "數量"])
    t = read("a.xlsx", data, Limits()).tables[0]
    assert t.series[0]["groups"] == [{"key": "2025-01", "count": 2, "sums": [15], "means": [7.5]},
                                     {"key": "2025-03", "count": 1, "sums": [7], "means": [7]}]
    [c] = ch.candidates([read("a.xlsx", data, Limits())])
    assert c.kind == "line" and c.title == "每月數量合計"


def test_period_keys_sorted():
    assert ch._is_period(["10月", "2月", "1月"]) and not ch._is_period(["A", "1月"])
    assert sorted(["10月", "2月", "1月"], key=ch._period_order) == ["1月", "2月", "10月"]


def test_svg_and_short_numbers():
    assert ch.short_number(11_754_100) == "1,175.4萬" and ch.short_number(370_000_000) == "3.7億"
    assert ch.short_number(45.0333) == "45.03" and ch.short_number(7) == "7"
    svg = ch.bar_svg([("台北<信義>", 10.0), ("B", -2.0)])
    assert svg.startswith("<svg") and "台北&lt;信義&gt;" in svg  # 文字有跳脫
    assert "<polyline" in ch.line_svg([("2025-01", 1.0), ("2025-02", 3.0)])


def test_mentioned_columns_come_first():
    data = book([["A", 1, 2, 3], ["A", 2, 3, 4], ["B", 5, 6, 7], ["B", 1, 1, 1]], ["分店", "營收", "人事成本", "NPS"])
    cands = ch.candidates([read("a.xlsx", data, Limits())])
    hit = ch.mentioned(cands, "著重人事成本與 nps")
    assert [c.title for c in hit] == ["依分店的人事成本合計", "依分店的NPS平均"]
    llm = [cands[0]]
    assert [c.title for c in ch.merge(hit, llm)] == ["依分店的人事成本合計", "依分店的NPS平均", "依分店的營收合計"]
