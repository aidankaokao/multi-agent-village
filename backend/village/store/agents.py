"""村民定義的 DB 存取：匯入內建範例、載入名冊。"""

import logging
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from village.core.card import AgentSpec
from village.core.registry import AgentRegistry, load_dir
from village.store.models import AgentDef

log = logging.getLogger("village.agents")


async def seed_builtin(sm: async_sessionmaker, root: Path, restore: bool = False) -> list[str]:
    """把 agents/ 的內建範例匯入 DB：只補 DB 沒有的 id，不會覆蓋使用者改過的內容。

    restore=True 時，連使用者刪掉的內建範例也一起救回來（內容回到範例原樣）。
    """
    specs, errors = load_dir(root)
    for folder, err in errors.items():
        log.warning("內建範例 agents/%s 無法匯入：%s", folder, err)
    added: list[str] = []
    async with sm() as s:
        for spec in specs:
            row = await s.get(AgentDef, spec.id)
            if row is None:
                s.add(AgentDef(id=spec.id, spec=spec.to_doc(), builtin=True))
            elif restore and row.deleted:
                row.spec, row.deleted, row.enabled = spec.to_doc(), False, True
            else:
                continue
            added.append(spec.id)
        await s.commit()
    return added


async def load_registry(sm: async_sessionmaker, registry: AgentRegistry) -> None:
    """把 DB 裡啟用中的村民載入名冊。"""
    specs: list[AgentSpec] = []
    errors: dict[str, str] = {}
    async with sm() as s:
        rows = await s.scalars(select(AgentDef).where(AgentDef.enabled, ~AgentDef.deleted).order_by(AgentDef.created_at))
        for row in rows:
            try:
                specs.append(AgentSpec.model_validate(row.spec))
            except ValidationError as e:  # 例如欄位規則改過，舊資料不再合法
                errors[row.id] = str(e)
    registry.replace(specs, errors)
