import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from village.api.chat import router as chat_router
from village.api.llm import router as llm_router
from village.api.villagers import router as villagers_router
from village.api.runs import router as runs_router
from village.api.workshop import router as workshop_router
from village.config import Settings
from village.core.executor import Runner
from village.core.registry import AgentRegistry
from village.events import EventBus
from village.llm.service import ClientFactory, LLMService
from village.security.admin import load_admin_token
from village.security.crypto import SecretBox, load_keyring
from village.security.redact import install_log_redaction
from village.store.agents import load_registry, seed_builtin
from village.store.db import init_db, make_engine, make_sessionmaker
from village.store.runs import TraceStore
from village.store.settings import RulesStore

log = logging.getLogger("village")


@dataclass
class AppContext:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker
    box: SecretBox
    llm: LLMService
    bus: EventBus
    admin_token: str | None
    registry: AgentRegistry
    runner: Runner
    rules: RulesStore
    trace: TraceStore
    tasks: set[asyncio.Task] = field(default_factory=set)
    active: dict[str, asyncio.Task] = field(default_factory=dict)  # run_id → 執行中的問答（取消用）


def create_app(
    settings: Settings | None = None, client_factory: ClientFactory | None = None, handoff_delay: float = 1.0
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        install_log_redaction()
        engine = make_engine(settings.database_url)
        await init_db(engine)
        sm = make_sessionmaker(engine)
        box = SecretBox(load_keyring(settings.config_dir))
        llm = LLMService(sm, box, client_factory)
        bus = EventBus()
        added = await seed_builtin(sm, settings.agents_dir)
        if added:
            log.info("匯入內建範例村民：%s", added)
        registry = AgentRegistry()
        await load_registry(sm, registry)
        rules = RulesStore(sm)
        await rules.load()
        trace = TraceStore(sm, keep=rules.current.keep_runs)
        if n := await trace.mark_interrupted():
            log.info("上次中斷的問答 %d 筆標記為 interrupted", n)
        app.state.ctx = AppContext(
            settings=settings,
            engine=engine,
            sessionmaker=sm,
            box=box,
            llm=llm,
            bus=bus,
            admin_token=load_admin_token(),
            registry=registry,
            runner=Runner(registry, llm, bus, handoff_delay=handoff_delay, rules=rules, trace=trace),
            rules=rules,
            trace=trace,
        )
        if app.state.ctx.admin_token is None:
            log.warning("未設定 VILLAGE_ADMIN_TOKEN：LLM 設定 API 不需驗證")
        yield
        for t in list(app.state.ctx.tasks):
            t.cancel()
        await engine.dispose()

    app = FastAPI(title="Multi-Agent Village", lifespan=lifespan)
    app.include_router(llm_router)
    app.include_router(chat_router)
    app.include_router(villagers_router)
    app.include_router(workshop_router)
    app.include_router(runs_router)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        bus: EventBus = socket.app.state.ctx.bus
        q = bus.subscribe()
        try:
            while True:
                await socket.send_json(await q.get())
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(q)

    return app


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
