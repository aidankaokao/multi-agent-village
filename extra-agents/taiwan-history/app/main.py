"""HTTP 服務。

公開（不用 token）：
- GET  /                               簡易問答網頁（測試用；網頁本身也要填 token 才能問）
- GET  /health                         健康檢查（索引建好了沒、正在補建的進度）
- GET  /.well-known/agent-card.json    A2A 自我介紹

需要存取 token（Authorization: Bearer …）：
- GET  /search?q=…      只檢索、不回答（不需要 LLM）
- POST /ask             問答：body {question, top_k?}
- POST /a2a             A2A JSON-RPC（message/send；message/stream 用 SSE 串流）

回答用的 LLM 由呼叫端在 header 帶進來：X-LLM-Base-URL、X-LLM-Model、X-LLM-API-Key（本機 Ollama 可省略）、
X-LLM-Reasoning-Effort（可省略）。沒帶 LLM 時只回傳找到的資料。
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import a2a, auth
from app.answer import ask, ask_stream
from app.config import Settings
from app.embedder import EmbedError, Embedder
from app.indexer import build_index
from app.llm import LLMConfig, LLMError, check_url
from app.store import Store

log = logging.getLogger("th")
STATIC = Path(__file__).parent / "static"


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)


def create_app(settings: Settings | None = None, embedder: Embedder | None = None) -> FastAPI:
    settings = settings or Settings()

    async def auto_index(app: FastAPI) -> None:
        state = app.state.indexing
        state.update(running=True, done=0, total=None, error=None)

        def progress(line: str) -> None:
            if line.startswith("[") and "/" in line:
                done, total = line[1 : line.index("]")].split("/")
                state.update(done=int(done), total=int(total))

        try:
            result = await build_index(settings, app.state.store, app.state.embedder, progress=progress)
            log.info("索引就緒：%s", result)
        except EmbedError as e:
            state["error"] = str(e)
            log.error("補建索引失敗：%s", e)
        finally:
            state["running"] = False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(settings.index_path)
        app.state.embedder = embedder or Embedder(settings.ollama_url, settings.embed_model)
        app.state.indexing = {"running": False, "done": 0, "total": None, "error": None}
        if not settings.access_tokens:
            log.warning("沒有設定 TH_ACCESS_TOKENS：問答與檢索會一律拒絕")
        task = asyncio.create_task(auto_index(app)) if settings.auto_index else None
        yield
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await app.state.embedder.aclose()
        app.state.store.close()

    app = FastAPI(title="台灣史專家", lifespan=lifespan)

    def require_token(request: Request) -> None:
        auth.check(request, settings.access_tokens)

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
        stats = app.state.store.stats()
        return {"ok": stats["chunks"] > 0, "auth_configured": bool(settings.access_tokens),
                "indexing": app.state.indexing, **stats}

    @app.get("/.well-known/agent-card.json")
    async def agent_card():
        return a2a.agent_card(settings)

    @app.get("/search", dependencies=[Depends(require_token)])
    async def search(q: str = Query(min_length=1, max_length=1000), k: int = Query(default=8, ge=1, le=30)):
        try:
            qv = (await app.state.embedder.embed([q]))[0]
        except EmbedError as e:
            raise HTTPException(503, str(e)) from None
        return {"query": q, "hits": [h.to_dict() for h in app.state.store.search(q, qv, k=k)]}

    async def answer(question: str, llm: LLMConfig | None, top_k: int | None) -> dict:
        try:
            a = await ask(question, app.state.store, app.state.embedder, llm, k=top_k or settings.top_k)
        except EmbedError as e:
            raise HTTPException(503, str(e)) from None
        return a.to_dict()

    @app.post("/ask", dependencies=[Depends(require_token)])
    async def ask_endpoint(body: AskBody, request: Request):
        return await answer(body.question, llm_from(request), body.top_k)

    @app.post("/a2a", dependencies=[Depends(require_token)])
    async def a2a_endpoint(request: Request):
        try:
            req = await request.json()
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "不是合法的 JSON"}})
        rid = req.get("id") if isinstance(req, dict) else None
        try:
            if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
                raise a2a.RpcError(-32600, "需要 JSON-RPC 2.0 格式")
            method = req.get("method")
            if method not in ("message/send", "message/stream"):
                raise a2a.RpcError(-32601, f"不支援的方法：{method}（支援 message/send、message/stream）")
            params = req.get("params") or {}
            question = a2a.question_from(params)
            llm = llm_from(request)
            if method == "message/stream":
                return StreamingResponse(
                    stream(rid, question, llm, params["message"].get("contextId")), media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            result = await answer(question, llm, None)
            return {"jsonrpc": "2.0", "id": rid, "result": a2a.reply(result, params["message"].get("contextId"))}
        except a2a.RpcError as e:
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": e.message}})

    async def stream(rid, question: str, llm: LLMConfig | None, context_id: str | None):
        """A2A message/stream：SSE，每個事件是一個 JSON-RPC 回應（result 是 Task / artifact-update / status-update）。"""
        task, text, data, done = a2a.stream_events(uuid.uuid4().hex, context_id or uuid.uuid4().hex)

        def sse(result: dict | None = None, error: dict | None = None) -> str:
            body = {"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})}
            return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

        yield sse(task())
        first = True
        try:
            async for kind, value in ask_stream(question, app.state.store, app.state.embedder, llm, k=settings.top_k):
                if kind == "delta":
                    yield sse(text(str(value), append=not first))
                    first = False
                else:
                    yield sse(data(a2a.answer_data(value.to_dict())))
            yield sse(done())
        except EmbedError as e:
            yield sse(error={"code": -32603, "message": str(e)})

    return app


app = create_app()
