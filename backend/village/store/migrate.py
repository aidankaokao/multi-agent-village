"""資料庫遷移（Alembic）。

啟動時自動執行 upgrade head：
  - 全新的 DB：依序建立所有資料表
  - Phase 5 之前用 create_all 建立的舊 DB（沒有 alembic_version）：先標記為 0001 基準版本，再往上升級，資料保留
開發者新增 migration：改好 models 後執行
  cd backend && uv run python -m village.store.migrate revision -m "說明"
"""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

HERE = Path(__file__).parent / "migrations"
BASELINE = "0001"


def _config(conn: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.attributes["connection"] = conn
    return cfg


def upgrade(conn: Connection) -> None:
    tables = set(inspect(conn).get_table_names())
    cfg = _config(conn)
    if "alembic_version" not in tables and "llm_providers" in tables:
        command.stamp(cfg, BASELINE)  # create_all 時代的舊 DB
    command.upgrade(cfg, "head")


def _cli() -> None:
    """產生新的 migration（autogenerate 對照一個升到最新的暫存 SQLite）。"""
    import argparse
    import tempfile

    from sqlalchemy import create_engine

    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["revision"])
    p.add_argument("-m", "--message", required=True)
    p.add_argument("--rev-id")
    a = p.parse_args()
    with tempfile.TemporaryDirectory() as d:
        engine = create_engine(f"sqlite:///{d}/m.db")
        with engine.begin() as conn:
            if any(HERE.joinpath("versions").glob("*.py")):
                command.upgrade(_config(conn), "head")
            command.revision(_config(conn), message=a.message, autogenerate=True, rev_id=a.rev_id)


if __name__ == "__main__":
    sys.exit(_cli())
