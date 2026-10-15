import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from village.api.llm import router as llm_router
from village.config import Settings
from village.events import EventBus
from village.llm.service import ClientFactory, LLMService
from village.security.admin import ADMIN_TOKEN_FILENAME, load_admin_token
from village.security.crypto import SecretBox, load_keyring
from village.security.redact import install_log_redaction
from village.store.db import init_db, make_engine, make_sessionmaker

log = logging.getLogger("village")


@dataclass
class AppContext:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker
    box: SecretBox
    llm: LLMService
    bus: EventBus
    admin_token: str


def create_app(settings: Settings | None = None, client_factory: ClientFactory | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        install_log_redaction()
        engine = make_engine(settings.database_url)
        await init_db(engine)
        sm = make_sessionmaker(engine)
        box = SecretBox(load_keyring(settings.config_dir))
        app.state.ctx = AppContext(
            settings=settings,
            engine=engine,
            sessionmaker=sm,
            box=box,
            llm=LLMService(sm, box, client_factory),
            bus=EventBus(),
            admin_token=load_admin_token(settings.config_dir),
        )
        log.warning("admin token：cat %s", settings.config_dir / ADMIN_TOKEN_FILENAME)
        yield
        await engine.dispose()

    app = FastAPI(title="Multi-Agent Village", lifespan=lifespan)
    app.include_router(llm_router)

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

    @app.post("/api/debug/demo-run")
    async def demo_run():
        """Phase 0 用：模擬「村長 → 詩人 → 笑話師」的事件序列，驗證村落動畫。"""

        async def play():
            bus: EventBus = app.state.ctx.bus
            run = "demo"
            await bus.publish("run.started", run_id=run, agent="mayor")
            await asyncio.sleep(1.5)
            await bus.publish("plan.created", run_id=run, agent="mayor", steps=["poet", "jester"])
            await bus.publish("agent.finished", run_id=run, agent="mayor")
            await bus.publish("agent.started", run_id=run, agent="poet")
            await asyncio.sleep(3)
            await bus.publish("agent.finished", run_id=run, agent="poet")
            await bus.publish("agent.handoff", run_id=run, from_agent="poet", to_agent="jester")
            await asyncio.sleep(2)
            await bus.publish("agent.started", run_id=run, agent="jester")
            await asyncio.sleep(3)
            await bus.publish("agent.finished", run_id=run, agent="jester")
            await bus.publish("run.finished", run_id=run)

        asyncio.create_task(play())
        return {"ok": True}

    return app


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
