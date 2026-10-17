"""從中文維基百科抓取 corpus/sources.yaml 列出的條目，整理成 Markdown 文件（corpus/docs/<時期>/<標題>.md）。

用法（在 extra-agents/taiwan-history/ 底下）：
    uv run python scripts/fetch_wiki.py            # 只抓還沒抓過的
    uv run python scripts/fetch_wiki.py --refresh  # 全部重新抓（維基內容更新時）
    uv run python scripts/fetch_wiki.py --only 霧社事件 二二八事件

做法：
  - 用 TextExtracts API 取得純文字（已去掉表格、資訊框、註腳編號），並轉成臺灣正體（variant=zh-tw）
  - 章節標題「== 標題 ==」轉成 Markdown 的 ##；去掉參考文獻、外部連結等不是內容的章節
  - 文件開頭記錄來源網址、版本（revision）、抓取日期、授權（CC BY-SA 4.0）
  - 遵守維基百科的使用規範：帶 User-Agent、一次一篇、每篇間隔，被限速（429）就等一下再試
"""

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "corpus" / "sources.yaml"
DOCS = ROOT / "corpus" / "docs"
API = "https://zh.wikipedia.org/w/api.php"
USER_AGENT = "taiwan-history-agent/0.1 (personal research project; https://github.com/aidankaokao/multi-agent-village)"
DELAY_S = 1.5  # 每篇之間的間隔，避免被限速

# 不是內容的章節：整段去掉
DROP_SECTIONS = {
    "參考文獻", "參考資料", "參考來源", "參考書目", "參考", "資料來源", "來源", "註釋", "注釋", "腳註", "註腳", "注解", "備註",
    "外部連結", "外部鏈接", "相關條目", "參見", "延伸閱讀", "擴展閱讀", "引用", "書目", "文獻", "註", "注",
}
HEADING = re.compile(r"^(=+)\s*(.*?)\s*\1$")
REF_TAG = re.compile(r"</?ref[^>]*>")  # 少數條目會殘留註腳標籤


def safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", title)


def to_markdown(title: str, extract: str) -> str:
    """維基純文字 → Markdown：章節轉 ##，去掉不是內容的章節與空章節。"""
    sections: list[tuple[int, str, list[str]]] = [(1, title, [])]
    for line in extract.splitlines():
        if m := HEADING.match(line.strip()):
            sections.append((len(m.group(1)), m.group(2), []))
        else:
            sections[-1][2].append(line.rstrip())
    kept: list[tuple[int, str, str]] = []
    drop_level = 0  # 正在略過的章節層級（它的子章節也一起略過）
    for level, name, lines in sections:
        if drop_level and level > drop_level:
            continue
        drop_level = 0
        if level > 1 and name in DROP_SECTIONS:
            drop_level = level
            continue
        # 維基純文字一行是一段：段落之間空一行（Markdown 才會分段，之後切塊也以段落為單位）
        body = "\n\n".join(clean for line in lines if (clean := REF_TAG.sub("", line).strip()))
        kept.append((level, name, body))
    # 從後往前：自己沒有內容、子章節也都沒有內容的標題拿掉
    has_content = [False] * len(kept)
    for i in range(len(kept) - 1, -1, -1):
        level, _, body = kept[i]
        child = False
        for j in range(i + 1, len(kept)):
            if kept[j][0] <= level:
                break
            child = child or has_content[j]
        has_content[i] = bool(body) or child
    out = []
    for i, (level, name, body) in enumerate(kept):
        if level > 1 and not has_content[i]:
            continue
        head = f"{'#' * min(level, 6)} {name}"
        out.append(f"{head}\n\n{body}" if body else head)
    text = "\n\n".join(out)
    return text.strip() + "\n"


def fetch(client: httpx.Client, title: str) -> dict:
    params = {
        "action": "query", "titles": title, "redirects": 1, "converttitles": 1, "prop": "extracts|info",
        "explaintext": 1, "exsectionformat": "wiki", "variant": "zh-tw", "format": "json", "formatversion": 2,
    }
    for attempt in range(5):
        r = client.get(API, params=params)
        if r.status_code == 429:
            wait = 15 * (attempt + 1)
            print(f"    被限速，等 {wait} 秒…", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        page = r.json()["query"]["pages"][0]
        if page.get("missing"):
            raise LookupError(f"維基百科沒有「{title}」這個條目")
        return page
    raise RuntimeError(f"「{title}」一直被限速，稍後再試")


def write_doc(period: dict, title: str, page: dict) -> Path:
    wiki_title = page["title"]
    meta = {
        "title": title,
        "wiki_title": wiki_title,
        "url": f"https://zh.wikipedia.org/zh-tw/{quote(wiki_title.replace(' ', '_'))}",
        "revision": page.get("lastrevid"),
        "fetched": date.today().isoformat(),
        "period": period["id"],
        "period_name": period["name"],
        "license": "CC BY-SA 4.0",
    }
    body = to_markdown(title, page.get("extract") or "")
    path = DOCS / period["id"] / f"{safe_name(title)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{front}\n---\n\n{body}", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="已經抓過的也重新抓")
    ap.add_argument("--only", nargs="*", help="只抓這幾個標題")
    args = ap.parse_args()

    src = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    jobs = [(p, t) for p in src["periods"] for t in p["titles"] if not args.only or t in args.only]
    failed: list[str] = []
    done = skipped = 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        for i, (period, title) in enumerate(jobs, 1):
            path = DOCS / period["id"] / f"{safe_name(title)}.md"
            if path.exists() and not args.refresh:
                skipped += 1
                continue
            try:
                page = fetch(client, title)
                out = write_doc(period, title, page)
                done += 1
                print(f"[{i}/{len(jobs)}] {title} → {out.relative_to(ROOT)}（{len(out.read_text(encoding='utf-8'))} 字）", flush=True)
            except Exception as e:  # 一篇失敗不影響其他篇
                failed.append(f"{title}：{e}")
                print(f"[{i}/{len(jobs)}] ✗ {title}：{e}", flush=True)
            time.sleep(DELAY_S)
    print(f"\n完成：新抓 {done} 篇、略過已存在 {skipped} 篇、失敗 {len(failed)} 篇")
    for f in failed:
        print("  ✗", f)
    (ROOT / "corpus" / "fetch_report.json").write_text(
        json.dumps({"date": date.today().isoformat(), "fetched": done, "skipped": skipped, "failed": failed},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
