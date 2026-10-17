"""資料文件的統計與品質檢查：各時期篇數與字數、最短 / 最長的文件、可疑的殘留雜訊。

用法：uv run python scripts/corpus_stats.py
"""

import re
from collections import defaultdict
from pathlib import Path

import yaml

DOCS = Path(__file__).resolve().parent.parent / "corpus" / "docs"
# 不該出現在乾淨文字裡的東西：模板、wiki 標記、HTML、數學式殘留、簡體常見字
SUSPICIOUS = {
    "模板殘留": re.compile(r"\{\{|\}\}"),
    "wiki 連結": re.compile(r"\[\[|\]\]"),
    "HTML 標籤": re.compile(r"</?[a-zA-Z][^>]{0,20}>"),
    "數學式": re.compile(r"\\displaystyle|\\frac"),
    "簡體字": re.compile(r"[们这说时国发对经过来为与关东问题动产开战]"),
}


def main() -> None:
    by_period: dict[str, list[tuple[str, int]]] = defaultdict(list)
    problems: dict[str, list[str]] = defaultdict(list)
    for path in sorted(DOCS.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        _, front, body = text.split("---", 2)
        meta = yaml.safe_load(front)
        chars = len(re.sub(r"\s", "", body))
        by_period[meta["period_name"]].append((meta["title"], chars))
        for name, pat in SUSPICIOUS.items():
            if n := len(pat.findall(body)):
                problems[name].append(f"{meta['title']}（{n}）")
    total_docs = sum(len(v) for v in by_period.values())
    total_chars = sum(c for v in by_period.values() for _, c in v)
    print(f"共 {total_docs} 篇、{total_chars:,} 字（不含空白）\n")
    for period, docs in by_period.items():
        print(f"  {period}：{len(docs)} 篇、{sum(c for _, c in docs):,} 字")
    flat = sorted((c, t) for v in by_period.values() for t, c in v)
    print("\n最短 5 篇：", "、".join(f"{t}（{c}）" for c, t in flat[:5]))
    print("最長 5 篇：", "、".join(f"{t}（{c:,}）" for c, t in flat[-5:]))
    print("\n品質檢查：")
    for name in SUSPICIOUS:
        items = problems.get(name, [])
        print(f"  {name}：{'沒有' if not items else f'{len(items)} 篇 — ' + '、'.join(items[:8]) + ('…' if len(items) > 8 else '')}")


if __name__ == "__main__":
    main()
