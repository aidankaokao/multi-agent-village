"""Alembic 環境：由 village.store.db.init_db 以程式呼叫（不需要 alembic.ini）。

連線由呼叫端放在 config.attributes["connection"]（同步連線，來自 AsyncEngine.run_sync）。
開發時想手動產生 migration：cd backend && uv run python -m village.store.migrate revision -m "說明"
"""

from alembic import context

from village.store import models  # noqa: F401  註冊所有 model
from village.store.db import Base

config = context.config
connection = config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=connection.dialect.name == "sqlite",  # SQLite 改欄位要用 batch 模式
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
