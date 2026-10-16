"""執行紀錄（登入即可，一般成員只看得到自己的；管理員看得到全部）與村規（管理員）API。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from village.security.auth import CurrentUser, require_admin, require_user
from village.store.models import RunTrace, User
from village.store.settings import VillageRules

router = APIRouter(prefix="/api", tags=["runs"])


class RunSummary(BaseModel):
    id: str
    user: str | None  # 提問者的顯示名稱
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


def _summary(r: RunTrace, user: str | None) -> dict:
    steps = r.steps or []
    reviews = r.reviews or []
    return dict(
        id=r.id, user=user, message=r.message, status=r.status, agents=[s.get("agent", "") for s in steps],
        review=reviews[-1].get("passed") if reviews else None, elapsed_ms=r.elapsed_ms,
        tokens_in=r.tokens_in or 0, tokens_out=r.tokens_out or 0, created_at=r.created_at,
    )


def _mine(stmt, me: CurrentUser):
    return stmt if me.is_admin else stmt.where(RunTrace.user_id == me.id)


@router.get("/runs")
async def list_runs(
    request: Request, limit: int = Query(50, ge=1, le=200), before: datetime | None = None, q: str | None = None,
    me: CurrentUser = Depends(require_user),
) -> list[RunSummary]:
    """新的在前；before 用來載入更多，q 搜尋問題內容。"""
    stmt = _mine(
        select(RunTrace, User.display_name).outerjoin(User, User.id == RunTrace.user_id)
        .order_by(RunTrace.created_at.desc()).limit(limit), me,
    )
    if before:
        stmt = stmt.where(RunTrace.created_at < before)
    if q:
        stmt = stmt.where(RunTrace.message.contains(q))
    async with request.app.state.ctx.sessionmaker() as s:
        return [RunSummary(**_summary(r, name)) for r, name in (await s.execute(stmt)).all()]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, me: CurrentUser = Depends(require_user)) -> RunDetail:
    stmt = _mine(select(RunTrace, User.display_name).outerjoin(User, User.id == RunTrace.user_id), me)
    async with request.app.state.ctx.sessionmaker() as s:
        row = (await s.execute(stmt.where(RunTrace.id == run_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這筆執行紀錄")
    r, name = row
    return RunDetail(
        **_summary(r, name), answer=r.answer, error=r.error, plan=r.plan, steps=r.steps or [], reviews=r.reviews or [],
        finished_at=r.finished_at,
    )


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: str, request: Request, me: CurrentUser = Depends(require_user)) -> None:
    async with request.app.state.ctx.sessionmaker() as s:
        await s.execute(_mine(delete(RunTrace).where(RunTrace.id == run_id), me))
        await s.commit()


@router.delete("/runs", status_code=status.HTTP_204_NO_CONTENT)
async def clear_runs(request: Request, me: CurrentUser = Depends(require_user)) -> None:
    """清空執行紀錄（執行中的不刪；一般成員只清自己的）。"""
    async with request.app.state.ctx.sessionmaker() as s:
        await s.execute(_mine(delete(RunTrace).where(RunTrace.status != "running"), me))
        await s.commit()


@router.get("/rules", dependencies=[Depends(require_admin)])
async def get_rules(request: Request) -> VillageRules:
    return request.app.state.ctx.rules.current


@router.put("/rules", dependencies=[Depends(require_admin)])
async def put_rules(body: VillageRules, request: Request) -> VillageRules:
    """村規：審核員、每步逾時、平行執行、紀錄保留筆數。下一次問答就生效。"""
    return await request.app.state.ctx.rules.save(body)
