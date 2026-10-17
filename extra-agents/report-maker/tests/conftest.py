import io
from datetime import date

import pytest
from openpyxl import Workbook


def make_pdf(lines: list[str]) -> bytes:
    """手工組一個最小的 PDF（Helvetica 英數字），不需要 WeasyPrint。lines 為空 = 沒有文字的 PDF。"""
    text = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({ln}) Tj 0 -16 Td" for ln in lines) + " ET" if lines else ""
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


@pytest.fixture
def sales_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "銷售"
    ws.append([None])  # 開頭空白列：標題列要往下找
    ws.append(["日期", "部門", "金額", "備註"])
    rows = [(date(2025, 1, 5), "業務部", 1000), (date(2025, 2, 1), "業務部", 2500), (date(2025, 3, 9), "研發部", 4000),
            (date(2025, 4, 2), "研發部", 500)]
    for d, dept, amt in rows:
        ws.append([d, dept, amt, None])
    wb.create_sheet("空的")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
