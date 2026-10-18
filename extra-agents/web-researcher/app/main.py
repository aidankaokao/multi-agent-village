"""HTTP 服務。

公開（不用 token）：
- GET  /                               簡易測試網頁（網頁本身也要填 token 才能用）
- GET  /health                         健康檢查（搜尋引擎 SearXNG 連得上嗎）
- GET  /.well-known/agent-card.json    A2A 自我介紹

需要存取 token（Authorization: Bearer …）：
- GET  /search?q=…      只搜尋、不讀網頁、不呼叫 LLM
- POST /ask             問答：body {question}（不串流）
- POST /a2a             A2A JSON-RPC（message/send；message/stream 用 SSE 串流）

回答用的 LLM 由呼叫端在 header 帶進來：X-LLM-Base-URL、X-LLM-Model、X-LLM-API-Key（本機 Ollama 可省略）、
X-LLM-Reasoning-Effort（可省略）。沒帶 LLM 時只列出找到的資料。
"""

import json
import logging
import uuid
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import a2a, auth
from app.config import Settings
from app.llm import LLMConfig, LLMError, check_url
from app.research import research, run
from app.search import SearchError, search

log = logging.getLogger("wr")
STATIC = Path(__file__).parent / "static"


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=a2a.MAX_QUESTION)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="網路研究員")
    if not settings.access_tokens:
        log.warning("沒有設定 WR_ACCESS_TOKENS：問答與搜尋會一律拒絕")

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
        try:
            async with httpx.AsyncClient(timeout=3.0) as http:
                r = await http.get(settings.searxng_url.rstrip("/") + "/healthz")
            ok, err = r.status_code == 200, None if r.status_code == 200 else f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            ok, err = False, f"連不上：{type(e).__name__}"
        return {"ok": ok, "search_error": err, "auth_configured": bool(settings.access_tokens)}

    @app.get("/.well-known/agent-card.json")
    async def agent_card():
        return a2a.agent_card(settings)

    @app.get("/search", dependencies=[Depends(require_token)])
    async def search_endpoint(q: str = Query(min_length=1, max_length=200)):
        try:
            hits = await search(settings.searxng_url, q, settings.language)
        except SearchError as e:
            raise HTTPException(503, str(e)) from None
        return {"query": q, "hits": [h.__dict__ | {"site": h.site} for h in hits]}

    @app.post("/ask", dependencies=[Depends(require_token)])
    async def ask_endpoint(body: AskBody, request: Request):
        try:
            return (await run(body.question, llm_from(request), settings)).to_dict()
        except SearchError as e:
            raise HTTPException(503, str(e)) from None

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
            context_id = params["message"].get("contextId")
            llm = llm_from(request)
            if method == "message/stream":
                return StreamingResponse(
                    stream(rid, question, llm, context_id), media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            try:
                result = await run(question, llm, settings)
            except SearchError as e:
                raise a2a.RpcError(-32603, str(e)) from None
            return {"jsonrpc": "2.0", "id": rid, "result": a2a.reply(result.to_dict(), context_id)}
        except a2a.RpcError as e:
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": e.message}})

    async def stream(rid, question: str, llm: LLMConfig | None, context_id: str | None):
        task, status, text, data, done = a2a.stream_events(uuid.uuid4().hex, context_id or uuid.uuid4().hex)

        def sse(result: dict | None = None, error: dict | None = None) -> str:
            body = {"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})}
            return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

        yield sse(task())
        first = True
        try:
            async for kind, value in research(question, llm, settings):
                if kind == "progress":
                    yield sse(status(str(value)))
                elif kind == "delta":
                    yield sse(text(str(value), append=not first))
                    first = False
                elif kind == "replace":
                    yield sse(text(str(value), append=False))
                    first = False
                else:
                    result = value.to_dict()
                    if first:  # 沒有串流過（沒有 LLM、找不到網頁）：整段送
                        yield sse(text(result["answer"], append=False))
                    yield sse(data(a2a.answer_data(result)))
            yield sse(done())
        except SearchError as e:
            yield sse(error={"code": -32603, "message": str(e)})
        except Exception as e:  # 不讓串流默默斷掉：一律回報給呼叫端
            log.exception("研究失敗")
            yield sse(error={"code": -32603, "message": f"研究失敗：{type(e).__name__}"})

    return app


app = create_app()
