"""抓取工具的 Markdown 轉換：章節層級、去掉參考文獻等章節、去掉空標題。"""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("fetch_wiki", Path(__file__).parent.parent / "scripts" / "fetch_wiki.py")
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)

EXTRACT = """導言第一段。
導言第二段</ref>。

== 背景 ==
背景內容。
=== 細節 ===
細節內容。
==== 原文 ====

=== 空的小節 ===

== 參考文獻 ==
一堆書目
=== 參考文獻的子章節 ===
更多書目
== 影響 ==
影響內容。
== 外部連結 ==
連結
"""


def test_to_markdown():
    md = fw.to_markdown("某事件", EXTRACT)
    assert md.startswith("# 某事件\n\n導言第一段。\n\n導言第二段。")  # 段落分開、去掉殘留標籤
    assert "## 背景\n\n背景內容。" in md and "### 細節\n\n細節內容。" in md and "## 影響\n\n影響內容。" in md
    for gone in ("原文", "空的小節", "參考文獻", "書目", "外部連結", "連結\n"):
        assert gone not in md, gone


def test_safe_name():
    assert fw.safe_name("縱貫線 (臺鐵)") == "縱貫線 (臺鐵)" and fw.safe_name("a/b:c") == "a_b_c"
