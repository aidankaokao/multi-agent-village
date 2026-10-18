"""指令（開發與測試用）。

    uv run wr search 2026 台灣電價
    uv run wr ask 2026 年台灣電價有調整嗎？ --llm-url http://localhost:11434/v1 --llm-model qwen3.5:9b --effort none
    uv run wr serve        # 啟動 HTTP 服務（預設 127.0.0.1:8102）
    uv run wr gen-token    # 產生一個隨機的存取 token（貼到 .env 的 WR_ACCESS_TOKENS）

搜尋引擎網址用環境變數 WR_SEARXNG_URL（本機開發預設 http://localhost:8888）。
問答用的 LLM 由參數或環境變數 WR_DEV_LLM_URL / WR_DEV_LLM_MODEL / WR_DEV_LLM_KEY 提供（只給指令列測試用）。
"""

import argparse
import asyncio
import json
import os

from app.config import Settings
from app.llm import LLMConfig


async def _run(args: argparse.Namespace) -> None:
    from app.research import research
    from app.search import search

    settings = Settings()
    if args.cmd == "search":
        for i, h in enumerate(await search(settings.searxng_url, args.question, settings.language), 1):
            print(f"[{i}] {h.title}（{h.site}{'・' + h.published if h.published else ''}）\n    {h.url}\n    {h.snippet[:120]}")
        return
    url = args.llm_url or os.environ.get("WR_DEV_LLM_URL")
    model = args.llm_model or os.environ.get("WR_DEV_LLM_MODEL")
    llm = LLMConfig(url, model, os.environ.get("WR_DEV_LLM_KEY", ""), args.effort) if url and model else None
    async for kind, value in research(args.question, llm, settings):
        if kind == "progress":
            print(f"… {value}")
        elif kind == "delta":
            print(value, end="", flush=True)
        elif kind == "replace":
            print(f"\n{value}")
        else:
            r = value.to_dict()
            if r["model"] is None:
                print(r["answer"])
            print("\n\n引用：")
            for c in r["citations"]:
                print(f"  [{c['n']}] 《{c['title']}》{c['section']}  {c['url']}")
            if r["failed"]:
                print("讀不到：", json.dumps(r["failed"], ensure_ascii=False))
            print(f"關鍵字：{r['queries']}　模型：{r['model']}　耗時：{r['timings']}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="wr", description="網路研究員")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("search", "ask"):
        p = sub.add_parser(name)
        p.add_argument("question")
        if name == "ask":
            p.add_argument("--llm-url")
            p.add_argument("--llm-model")
            p.add_argument("--effort", help="思考強度：none / low / medium / high")
    sub.add_parser("gen-token", help="產生隨機的存取 token")
    p = sub.add_parser("serve", help="啟動 HTTP 服務")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8102)
    args = ap.parse_args()
    if args.cmd == "gen-token":
        from app.auth import new_token

        print(new_token())
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("app.main:app", host=args.host, port=args.port)
    else:
        asyncio.run(_run(args))
