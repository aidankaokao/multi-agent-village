import pytest

AI = """根據最新資料，以下是最近一週的 AI 新聞：

### 1. 國產大模型
* 國內開發的大模型攻克數學難題。[1]
* 全球 AI 支出在 2026 年預計達 2.52 兆美元。[2]

### 2. 資安
* 臺灣受網路威脅影響程度升至全球第 4 名，一年 193 起國家級駭客活動。[2]

資料來源：
[1] 《每日科技要聞》capitalfutures.com.tw・2026-10-18　https://example.com/a
[2] 《資安週報》ithome.com.tw・2026-10-08　https://example.com/b
"""

STOCK = """### 台股
台股單周重挫 2,202 點，跌幅 4.47%。[1]

資料來源：
[1] 《台股週報》news.cnyes.com　https://example.com/c
"""


@pytest.fixture
def files():
    return [("網路研究員_AI新聞.txt", AI.encode()), ("網路研究員_台股.txt", STOCK.encode())]


@pytest.fixture(autouse=True)
def no_render(monkeypatch, request):
    """預設不真的排版（本機可能沒有 Pango）；需要真的排版的測試標記 real_render。"""
    if "real_render" in request.keywords:
        return
    import app.render as r

    monkeypatch.setattr(r, "render_pdf", lambda paper: b"%PDF-1.7 fake")
    monkeypatch.setattr(r, "render_preview", lambda pdf: b"\x89PNG fake")
