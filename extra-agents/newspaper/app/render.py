"""報紙 → HTML 樣板（Jinja2）→ PDF（WeasyPrint，A4 直式多頁）→ 第一版預覽圖（pypdfium2 轉 PNG）。

中文字型：容器裡裝 Noto Serif / Sans CJK（fonts-noto-cjk）；本機開發用系統字型（macOS 的宋體、蘋方）。
"""

import io
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from app.paper import Paper

TEMPLATES = Path(__file__).parent / "templates"
PREVIEW_WIDTH = 1100  # 預覽圖寬度（px）


def cite(text: str) -> Markup:
    """內文的 [3] 變成上標的來源編號，相連的 [1][2] 合成「1,2」（先跳脫，LLM 的輸出不可信）。"""
    def sup(m: re.Match) -> str:
        return f'<sup class="cite">{",".join(re.findall(r"\d+", m.group(0)))}</sup>'

    return Markup(re.sub(r"(?:\[\d{1,3}\])+", sup, str(escape(text))))


_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
_env.filters["cite"] = cite


def render_html(paper: Paper) -> str:
    return _env.get_template("paper.html").render(p=paper)


def render_pdf(paper: Paper) -> bytes:
    from weasyprint import HTML  # 延後載入：沒有 Pango 的環境也能跑其他測試

    return HTML(string=render_html(paper), base_url=str(TEMPLATES)).write_pdf()


def render_preview(pdf: bytes) -> bytes:
    """第一版轉成 PNG（給聊天視窗直接顯示）。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        page = doc[0]
        scale = PREVIEW_WIDTH / page.get_width()
        image = page.render(scale=scale).to_pil().convert("RGB")
    finally:
        doc.close()
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def page_count(pdf: bytes) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        return len(doc)
    finally:
        doc.close()
