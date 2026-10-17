"""指令（開發與測試用）。

    uv run rmk read 檔案…                         # 只讀檔，印出程式整理的內容（給 LLM 看的文件摘要）
    uv run rmk report 檔案… -q "著重成本" -o 報告.pdf --llm-url http://localhost:11434/v1 --llm-model qwen3.5:9b
    uv run rmk serve                              # 啟動 HTTP 服務（預設 127.0.0.1:8101）
    uv run rmk gen-token                          # 產生一個隨機的存取 token（貼到 .env 的 RM_ACCESS_TOKENS）

報告用的 LLM 由參數或環境變數 RM_DEV_LLM_URL / RM_DEV_LLM_MODEL / RM_DEV_LLM_KEY 提供（只給指令列測試用，服務本身不讀這些）。
沒給 LLM 時產生「統計版」報告。macOS 本機要先 export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib（WeasyPrint 找 Pango 用）。
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.config import Settings
from app.llm import LLMConfig


async def _run(args: argparse.Namespace) -> None:
    from app.readers import read
    from app.report import digest, run

    settings = Settings()
    files = [(Path(f).name, Path(f).read_bytes()) for f in args.files]
    if args.cmd == "read":
        for i, (name, data) in enumerate(files, 1):
            doc = read(name, data, settings.limits)
            print(await digest(doc, i, None, settings.doc_chars, None, []))
            print()
        return
    url = args.llm_url or os.environ.get("RM_DEV_LLM_URL")
    model = args.llm_model or os.environ.get("RM_DEV_LLM_MODEL")
    llm = LLMConfig(url, model, os.environ.get("RM_DEV_LLM_KEY", ""), args.effort) if url and model else None

    async def progress(msg: str) -> None:
        print(f"… {msg}")

    report, pdf = await run(files, args.question, llm, settings, progress)
    Path(args.out).write_bytes(pdf)
    print()
    print(report.to_markdown())
    print(f"\n模型：{report.model}　耗時：{json.dumps(report.timings, ensure_ascii=False)}")
    print(f"PDF：{args.out}（{len(pdf) // 1024} KB）")


def main() -> None:
    ap = argparse.ArgumentParser(prog="rmk", description="文件彙整師")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("read", help="只讀檔，印出整理後的內容")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("report", help="產生報告 PDF")
    p.add_argument("files", nargs="+")
    p.add_argument("-q", "--question", default="")
    p.add_argument("-o", "--out", default="report.pdf")
    p.add_argument("--llm-url")
    p.add_argument("--llm-model")
    p.add_argument("--effort", help="思考強度：none / low / medium / high")
    sub.add_parser("gen-token", help="產生隨機的存取 token")
    p = sub.add_parser("serve", help="啟動 HTTP 服務")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8101)
    args = ap.parse_args()
    if args.cmd == "gen-token":
        from app.auth import new_token

        print(new_token())
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("app.main:app", host=args.host, port=args.port)
    else:
        asyncio.run(_run(args))
