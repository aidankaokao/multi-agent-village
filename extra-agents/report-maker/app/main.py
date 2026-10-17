"""HTTP 服務。

公開（不用 token）：
- GET  /                               簡易測試網頁（網頁本身也要填 token 才能用）
- GET  /health                         健康檢查（PDF 產生器能不能用）
- GET  /.well-known/agent-card.json    A2A 自我介紹

需要存取 token（Authorization: Bearer …）：
- POST /a2a      A2A JSON-RPC（message/send；message/stream 用 SSE 串流進度）

報告用的 LLM 由呼叫端在 header 帶進來：X-LLM-Base-URL、X-LLM-Model、X-LLM-API-Key（本機 Ollama 可省略）、
X-LLM-Reasoning-Effort（可省略）。沒帶 LLM 時產生「統計版」報告。
上傳的檔案只在記憶體處理，不存檔（所以不提供 multipart 上傳：框架會把大檔先寫進暫存檔）；同時處理的報告數有上限（RM_MAX_CONCURRENT），其餘排隊。
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app import a2a, auth
from app.config import Settings
from app.llm import LLMConfig, LLMError, check_url
from app.readers import ReadError
from app.report import run

log = logging.getLogger("rm")
STATIC = Path(__file__).parent / "static"


def _pdf_ready() -> str | None:
    try:
        import weasyprint  # noqa: F401
    except Exception as e:  # 缺 Pango 等系統函式庫
        return f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
    return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="文件彙整師")
    gate = asyncio.Semaphore(int(os.environ.get("RM_MAX_CONCURRENT", "2")))
    # base64 會讓檔案變大約 4/3，再加一點訊息本身的空間
    max_body = settings.limits.max_total_bytes * 4 // 3 + 1024 * 1024
    if not settings.access_tokens:
        log.warning("沒有設定 RM_ACCESS_TOKENS：產生報告的呼叫會一律拒絕")

    def require_token(request: Request) -> None:
        auth.check(request, settings.access_tokens)

    def check_size(request: Request) -> None:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_body:
            raise HTTPException(413, f"上傳內容太大（檔案合計上限 {settings.limits.max_total_bytes // 1024 // 1024} MB）")

    def llm_from(request: Request) -> LLMConfig | None:
        h = request.headers
        url, model = h.get("x-llm-base-url"), h.get("x-llm-model")
        if not (url and model):
            return None
        try:
            check_url(url, settings.allowed_llm_hosts)
        except LLMError as e:
            raise HTTPException(400, str(e)) from None
        return LLMConfig(url, model, h.get("x-llm-api-key") or "", h.get("x-llm-reasoning-effort") or None)

    @app.get("/", include_in_schema=False)
    async def index_page():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health():
        err = _pdf_ready()
        return {"ok": err is None, "pdf_error": err, "auth_configured": bool(settings.access_tokens),
                "limits": {"max_files": settings.limits.max_files, "max_file_mb": settings.limits.max_file_bytes // 1024 // 1024,
                           "max_total_mb": settings.limits.max_total_bytes // 1024 // 1024}}

    @app.get("/.well-known/agent-card.json")
    async def agent_card():
        return a2a.agent_card(settings)

    @app.post("/a2a", dependencies=[Depends(require_token), Depends(check_size)])
    async def a2a_endpoint(request: Request):
        chunks, size = [], 0
        async for chunk in request.stream():  # 邊收邊算大小（沒有 Content-Length 的請求也擋得住）
            size += len(chunk)
            if size > max_body:
                raise HTTPException(413, f"上傳內容太大（檔案合計上限 {settings.limits.max_total_bytes // 1024 // 1024} MB）")
            chunks.append(chunk)
        body = b"".join(chunks)
        del chunks
        try:
            req = json.loads(body)
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "不是合法的 JSON"}})
        del body
        rid = req.get("id") if isinstance(req, dict) else None
        try:
            if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
                raise a2a.RpcError(-32600, "需要 JSON-RPC 2.0 格式")
            method = req.get("method")
            if method not in ("message/send", "message/stream"):
                raise a2a.RpcError(-32601, f"不支援的方法：{method}（支援 message/send、message/stream）")
            params = req.get("params") or {}
            question, files = a2a.request_from(params)
            context_id = params["message"].get("contextId")
            llm = llm_from(request)
            if method == "message/stream":
                return StreamingResponse(
                    stream(rid, question, files, llm, context_id), media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            async with gate:
                try:
                    report, pdf = await run(files, question, llm, settings)
                except ReadError as e:
                    raise a2a.RpcError(-32602, str(e)) from None
            return {"jsonrpc": "2.0", "id": rid, "result": a2a.reply(report.to_dict(), report.to_markdown(), pdf, context_id)}
        except a2a.RpcError as e:
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": e.message}})

    async def stream(rid, question: str, files: list[tuple[str, bytes]], llm: LLMConfig | None, context_id: str | None):
        """A2A message/stream：SSE，每個事件是一個 JSON-RPC 回應。進度用 status-update（final=false）送。"""
        task, status, artifact, done = a2a.stream_events(uuid.uuid4().hex, context_id or uuid.uuid4().hex)
        queue: asyncio.Queue = asyncio.Queue()

        def sse(result: dict | None = None, error: dict | None = None) -> str:
            body = {"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})}
            return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

        async def progress(msg: str) -> None:
            await queue.put(("progress", msg))

        async def work() -> None:
            try:
                if gate.locked():
                    await progress("排隊中（同時處理的報告數已滿）")
                async with gate:
                    await queue.put(("done", await run(files, question, llm, settings, progress)))
            except ReadError as e:
                await queue.put(("error", {"code": -32602, "message": str(e)}))
            except Exception as e:  # 不讓背景工作默默消失：一律回報給呼叫端
                log.exception("產生報告失敗")
                await queue.put(("error", {"code": -32603, "message": f"產生報告失敗：{type(e).__name__}"}))

        yield sse(task())
        job = asyncio.create_task(work())
        try:
            while True:
                kind, value = await queue.get()
                if kind == "progress":
                    yield sse(status(value))
                elif kind == "error":
                    yield sse(error=value)
                    return
                else:
                    report, pdf = value
                    text, file, data = a2a.parts(report.to_dict(), report.to_markdown(), pdf)
                    yield sse(artifact("report", text))
                    yield sse(artifact("report-pdf", file))
                    yield sse(artifact("report-data", data, last=True))
                    yield sse(done())
                    return
        finally:
            if not job.done():  # 呼叫端斷線：停止產生
                job.cancel()

    return app


app = create_app()
