"""指令（開發與測試用）。

    uv run np make 資料.txt… -q "科技早報" -o 報紙.pdf --llm-url http://localhost:11434/v1 --llm-model qwen3.5:9b
    uv run np serve                              # 啟動 HTTP 服務（預設 127.0.0.1:8103）
    uv run np gen-token                          # 產生一個隨機的存取 token（貼到 .env 的 NP_ACCESS_TOKENS）

寫稿用的 LLM 由參數或環境變數 NP_DEV_LLM_URL / NP_DEV_LLM_MODEL / NP_DEV_LLM_KEY 提供（只給指令列測試用，服務本身不讀這些）。
沒給 LLM 時每份資料直接排成一則新聞。macOS 本機要先 export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib（WeasyPrint 找 Pango 用）。
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.config import Settings
from app.llm import LLMConfig


async def _make(args: argparse.Namespace) -> None:
    from app.paper import run

    files = [(Path(f).name, Path(f).read_bytes()) for f in args.files]
    url = args.llm_url or os.environ.get("NP_DEV_LLM_URL")
    model = args.llm_model or os.environ.get("NP_DEV_LLM_MODEL")
    llm = LLMConfig(url, model, os.environ.get("NP_DEV_LLM_KEY", ""), args.effort) if url and model else None

    async def progress(msg: str) -> None:
        print(f"… {msg}")

    paper, pdf, png = await run(files, args.question, llm, Settings(), progress)
    out = Path(args.out)
    out.write_bytes(pdf)
    out.with_suffix(".png").write_bytes(png)
    print()
    print(paper.to_markdown())
    print(f"\n模型：{paper.model}　LLM 錯誤：{paper.llm_error}　耗時：{json.dumps(paper.timings, ensure_ascii=False)}")
    if paper.unverified:
        print("找不到的數字：", "；".join(paper.unverified))
    print(f"PDF：{out}（{len(pdf) // 1024} KB）　預覽：{out.with_suffix('.png')}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="np", description="報社")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make", help="把採訪資料編成報紙 PDF")
    p.add_argument("files", nargs="+")
    p.add_argument("-q", "--question", default="")
    p.add_argument("-o", "--out", default="paper.pdf")
    p.add_argument("--llm-url")
    p.add_argument("--llm-model")
    p.add_argument("--effort", help="思考強度：none / low / medium / high")
    sub.add_parser("gen-token", help="產生隨機的存取 token")
    p = sub.add_parser("serve", help="啟動 HTTP 服務")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8103)
    args = ap.parse_args()
    if args.cmd == "gen-token":
        from app.auth import new_token

        print(new_token())
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("app.main:app", host=args.host, port=args.port)
    else:
        asyncio.run(_make(args))
