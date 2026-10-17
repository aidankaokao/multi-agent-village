"""報告 → HTML 樣板（Jinja2）→ PDF（WeasyPrint）。

中文字型：容器裡裝 Noto Sans CJK TC（fonts-noto-cjk）；本機開發用系統字型（macOS 的蘋方）。沒有中文字型會變成方塊。
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.report import Report, _num

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
_env.filters["num"] = _num


def render_html(report: Report) -> str:
    return _env.get_template("report.html").render(r=report, paragraphs=[p.strip() for p in report.summary.split("\n\n") if p.strip()])


def render_pdf(report: Report) -> bytes:
    from weasyprint import HTML  # 延後載入：沒有 Pango 的環境也能跑讀檔與測試

    return HTML(string=render_html(report), base_url=str(TEMPLATES)).write_pdf()
