"""HTTP 服務。

- GET  /health          健康檢查（索引有沒有建好）
- GET  /search?q=…      只檢索、不回答（除錯用，不需要 LLM）
- POST /ask             問答：body {question, top_k?}；回答用的 LLM 由呼叫端在 header 帶進來：
                          X-LLM-Base-URL、X-LLM-Model、X-LLM-API-Key（可省略，例如本機 Ollama）、X-LLM-Reasoning-Effort（可省略）
                        沒帶 LLM 時只回傳找到的資料

存取 token、A2A 介面、簡易問答網頁在 T3 加上。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.answer import ask
from app.config import Settings
from app.embedder import EmbedError, Embedder
from app.llm import LLMConfig, LLMError, check_url
from app.store import Store


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)


def create_app(settings: Settings | None = None, embedder: Embedder | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(settings.index_path)
        app.state.embedder = embedder or Embedder(settings.ollama_url, settings.embed_model)
        yield
        await app.state.embedder.aclose()
        app.state.store.close()

    app = FastAPI(title="台灣史專家", lifespan=lifespan)

    @app.get("/health")
    async def health():
        stats = app.state.store.stats()
        return {"ok": stats["chunks"] > 0, **stats}

    @app.get("/search")
    async def search(q: str = Query(min_length=1, max_length=1000), k: int = Query(default=8, ge=1, le=30)):
        try:
            qv = (await app.state.embedder.embed([q]))[0]
        except EmbedError as e:
            raise HTTPException(503, str(e)) from None
        return {"query": q, "hits": [h.to_dict() for h in app.state.store.search(q, qv, k=k)]}

    @app.post("/ask")
    async def ask_endpoint(
        body: AskBody,
        x_llm_base_url: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_key: str | None = Header(default=None),
        x_llm_reasoning_effort: str | None = Header(default=None),
    ):
        llm = None
        if x_llm_base_url and x_llm_model:
            try:
                check_url(x_llm_base_url, settings.allowed_llm_hosts)
            except LLMError as e:
                raise HTTPException(400, str(e)) from None
            llm = LLMConfig(x_llm_base_url, x_llm_model, x_llm_api_key or "", x_llm_reasoning_effort)
        try:
            a = await ask(body.question, app.state.store, app.state.embedder, llm, k=body.top_k or settings.top_k)
        except EmbedError as e:
            raise HTTPException(503, str(e)) from None
        return a.to_dict()

    return app


app = create_app()
