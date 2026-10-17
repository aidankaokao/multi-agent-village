import io
import zipfile

import pytest

from app.config import Limits
from app.readers import ReadError, check_batch, decode, read
from tests.conftest import make_pdf

L = Limits()


def test_xlsx_stats_and_groups(sales_xlsx):
    doc = read("銷售.xlsx", sales_xlsx, L)
    assert [t.name for t in doc.tables] == ["銷售"]  # 空的工作表略過
    t = doc.tables[0]
    assert t.rows == 4
    cols = {c.name: c for c in t.columns}
    assert cols["金額"].numeric == {"count": 4, "sum": 8000, "mean": 2000, "min": 500, "max": 4000}
    assert cols["日期"].dates == {"min": "2025-01-05", "max": "2025-04-02"}
    assert cols["部門"].top == [("業務部", 2), ("研發部", 2)]
    assert t.groups == [{"by": "部門", "sums": ["金額"], "groups": [
        {"key": "業務部", "count": 2, "sums": [3500], "means": [1750]},
        {"key": "研發部", "count": 2, "sums": [4500], "means": [2250]}]}]


def test_xlsx_row_limit(sales_xlsx):
    doc = read("銷售.xlsx", sales_xlsx, Limits(max_rows=2))
    assert doc.tables[0].rows == 2 and doc.tables[0].truncated
    assert any("2 列" in n for n in doc.notes)


def test_xlsx_zip_bomb_and_bad_file(sales_xlsx):
    with pytest.raises(ReadError, match="解壓後"):
        read("a.xlsx", sales_xlsx, Limits(max_unzipped_bytes=100))
    with pytest.raises(ReadError, match="不是有效的 xlsx"):
        read("a.xlsx", b"not a zip", L)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.txt", "hello")
    with pytest.raises(ReadError, match="讀不出來"):
        read("a.xlsx", buf.getvalue(), L)


def test_csv_big5_with_thousands_separator():
    data = "月份,項目,費用\n1月,租金,\"120,000\"\n1月,水電,8500\n2月,租金,\"120,000\"\n".encode("cp950")
    doc = read("費用.csv", data, L)
    assert doc.encoding == "Big5"
    cols = {c.name: c for c in doc.tables[0].columns}
    assert cols["費用"].numeric["sum"] == 248500


def test_csv_semicolon_delimiter():
    doc = read("a.csv", "name;value\na;1\nb;2\n".encode(), L)
    assert [c.name for c in doc.tables[0].columns] == ["name", "value"]


def test_decode_encodings():
    assert decode("中文".encode("utf-8-sig")) == ("中文", "UTF-8")
    assert decode("中文".encode("cp950"))[1] == "Big5"
    assert decode("中文".encode("utf-16")) == ("中文", "UTF-16")


def test_txt():
    doc = read("a.txt", "第一行\r\n第二行\r\n".encode(), L)
    assert doc.text == "第一行\n第二行"
    with pytest.raises(ReadError, match="空的"):
        read("b.txt", b"   \n", L)


def test_pdf_text_and_empty():
    doc = read("a.pdf", make_pdf(["Quarterly revenue 12345", "Cost 678"]), L)
    assert doc.pages == 1
    assert "Quarterly revenue 12345" in doc.text and doc.text.startswith("〔第 1 頁〕")
    with pytest.raises(ReadError, match="沒有文字"):
        read("b.pdf", make_pdf([]), L)
    with pytest.raises(ReadError, match="不是有效的 PDF"):
        read("c.pdf", b"%PDF-garbage", L)


def test_check_batch():
    with pytest.raises(ReadError, match="最多 3 份"):
        check_batch([("a.txt", b"x")] * 4, L)
    with pytest.raises(ReadError, match="另存成 .xlsx"):
        check_batch([("a.xls", b"x")], L)
    with pytest.raises(ReadError, match="超過每份"):
        check_batch([("a.txt", b"x" * 11)], Limits(max_file_bytes=10))
    with pytest.raises(ReadError, match="合計"):
        check_batch([("a.txt", b"x" * 8), ("b.txt", b"x" * 8)], Limits(max_file_bytes=10, max_total_bytes=15))
    with pytest.raises(ReadError, match="空檔案"):
        check_batch([("a.txt", b"")], L)
