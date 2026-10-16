"""執行紀錄與村規 API（設定類，有設定 admin token 時需要驗證）。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from village.security.admin import require_admin
from village.store.models import RunTrace
from village.store.settings import VillageRules

router = APIRouter(prefix="/api", tags=["runs"], dependencies=[Depends(require_admin)])


class RunSummary(BaseModel):
    id: str
    message: str
    status: str
    agents: list[str]  # 參與的村民（依步驟順序）
    review: bool | None  # 最後一次審核結果；None = 沒審核
    elapsed_ms: int | None
    tokens_in: int
    tokens_out: int
    created_at: datetime


class RunDetail(RunSummary):
    answer: str | None
    error: str | None
    plan: dict | None
    steps: list
    reviews: list
    finished_at: datetime | None


def _summary(r: RunTrace) -> dict:
    steps = r.steps or []
    reviews = r.reviews or []
    return dict(
        id=r.id, message=r.message, status=r.status, agents=[s.get("agent", "") for s in steps],
        review=reviews[-1].get("passed") if reviews else None, elapsed_ms=r.elapsed_ms,
        tokens_in=r.tokens_in or 0, tokens_out=r.tokens_out or 0, created_at=r.created_at,
    )


@router.get("/runs")
async def list_runs(
    request: Request, limit: int = Query(50, ge=1, le=200), before: datetime | None = None, q: str | None = None
) -> list[RunSummary]:
    """新的在前；before 用來載入更多，q 搜尋問題內容。"""
    stmt = select(RunTrace).order_by(RunTrace.created_at.desc()).limit(limit)
    if before:
        stmt = stmt.where(RunTrace.created_at < before)
    if q:
        stmt = stmt.where(RunTrace.message.contains(q))
    async with request.app.state.ctx.sessionmaker() as s:
        return [RunSummary(**_summary(r)) for r in await s.scalars(stmt)]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> RunDetail:
    async with request.app.state.ctx.sessionmaker() as s:
        r = await s.get(RunTrace, run_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這筆執行紀錄")
    return RunDetail(
        **_summary(r), answer=r.answer, error=r.error, plan=r.plan, steps=r.steps or [], reviews=r.reviews or [],
        finished_at=r.finished_at,
    )


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: str, request: Request) -> None:
    async with request.app.state.ctx.sessionmaker() as s:
        await s.execute(delete(RunTrace).where(RunTrace.id == run_id))
        await s.commit()


@router.delete("/runs", status_code=status.HTTP_204_NO_CONTENT)
async def clear_runs(request: Request) -> None:
    """清空執行紀錄（執行中的不刪）。"""
    async with request.app.state.ctx.sessionmaker() as s:
        await s.execute(delete(RunTrace).where(RunTrace.status != "running"))
        await s.commit()


@router.get("/rules")
async def get_rules(request: Request) -> VillageRules:
    return request.app.state.ctx.rules.current


@router.put("/rules")
async def put_rules(body: VillageRules, request: Request) -> VillageRules:
    """村規：審核員、每步逾時、平行執行、紀錄保留筆數。下一次問答就生效。"""
    return await request.app.state.ctx.rules.save(body)
