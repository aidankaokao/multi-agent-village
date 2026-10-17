"""指令：建索引、檢索、問答（開發與測試用）。

    uv run th index            # 只處理有變動的文件
    uv run th index --rebuild  # 整個重建
    uv run th search 霧社事件的起因
    uv run th ask 霧社事件的起因是什麼？ --llm-url http://localhost:11434/v1 --llm-model qwen3.5:9b
    uv run th serve            # 啟動 HTTP 服務（預設 127.0.0.1:8100）
    uv run th gen-token        # 產生一個隨機的存取 token（貼到 .env 的 TH_ACCESS_TOKENS）

問答用的 LLM 由參數或環境變數 TH_DEV_LLM_URL / TH_DEV_LLM_MODEL / TH_DEV_LLM_KEY 提供（只給指令列測試用，服務本身不讀這些）。
"""

import argparse
import asyncio
import json
import os

from app.answer import ask
from app.config import Settings
from app.embedder import Embedder
from app.indexer import build_index
from app.llm import LLMConfig
from app.store import Store


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()
    store = Store(settings.index_path)
    embedder = Embedder(settings.ollama_url, settings.embed_model)
    try:
        if args.cmd == "index":
            print(json.dumps(await build_index(settings, store, embedder, rebuild=args.rebuild), ensure_ascii=False))
        elif args.cmd == "search":
            qv = (await embedder.embed([args.question]))[0]
            for i, h in enumerate(store.search(args.question, qv, k=args.k), 1):
                print(f"[{i}] {h.score:.4f} 向量#{h.vector_rank} 關鍵字#{h.keyword_rank}  《{h.doc_title}》{h.section}")
                print(f"     {h.text[:120]}…")
        elif args.cmd == "ask":
            url = args.llm_url or os.environ.get("TH_DEV_LLM_URL")
            model = args.llm_model or os.environ.get("TH_DEV_LLM_MODEL")
            llm = LLMConfig(url, model, os.environ.get("TH_DEV_LLM_KEY", ""), args.effort) if url and model else None
            a = await ask(args.question, store, embedder, llm, k=args.k)
            print(a.answer)
            print("\n引用：")
            for c in a.citations:
                print(f"  [{c['n']}] 《{c['title']}》{c['section']}  {c['url']}")
            print(f"\n模型：{a.model}　耗時：{a.timings}")
    finally:
        await embedder.aclose()
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="th", description="台灣史專家")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index", help="建索引")
    p.add_argument("--rebuild", action="store_true")
    for name in ("search", "ask"):
        p = sub.add_parser(name)
        p.add_argument("question")
        p.add_argument("-k", type=int, default=8)
        if name == "ask":
            p.add_argument("--llm-url")
            p.add_argument("--llm-model")
            p.add_argument("--effort", help="思考強度：none / low / medium / high")
    sub.add_parser("gen-token", help="產生隨機的存取 token")
    p = sub.add_parser("serve", help="啟動 HTTP 服務")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8100)
    args = ap.parse_args()
    if args.cmd == "gen-token":
        from app.auth import new_token

        print(new_token())
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("app.main:app", host=args.host, port=args.port)
    else:
        asyncio.run(_run(args))
