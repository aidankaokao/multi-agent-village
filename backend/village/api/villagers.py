"""村民管理 API：在介面上新增 / 編輯 / 停用 / 刪除村民，以及試打、跑測試題、匯入匯出 YAML。

和 LLM 設定一樣屬於設定類 API（有設定 admin token 時需要驗證）。
問答用的公開村民清單在 api/chat.py 的 GET /api/agents。
"""

import time
from datetime import datetime

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from village.core.card import AgentSpec, Expect
from village.core.executor import StepResult, run_agent
from village.core.harness import check_expect
from village.core.registry import RESERVED_IDS
from village.llm.base import LLMError
from village.security.auth import require_admin
from village.store.agents import load_registry, seed_builtin
from village.store.models import AgentDef
from village.tools import TOOLS

router = APIRouter(prefix="/api/villagers", tags=["villagers"], dependencies=[Depends(require_admin)])


class VillagerOut(BaseModel):
    id: str
    enabled: bool
    builtin: bool
    spec: dict
    updated_at: datetime

    @classmethod
    def of(cls, row: AgentDef) -> "VillagerOut":
        return cls(id=row.id, enabled=row.enabled, builtin=row.builtin, spec=row.spec, updated_at=row.updated_at)


class SpecBody(BaseModel):
    spec: dict


class EnabledBody(BaseModel):
    enabled: bool


class YamlBody(BaseModel):
    yaml: str = Field(max_length=100_000)


class TryBody(BaseModel):
    spec: dict  # 可以是還沒儲存的草稿，方便邊改邊試
    instruction: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=20_000)
    target: str | None = None  # 'tier:large' / 'tier:small' / 模型 id；不填則依村民設定
    expect: Expect | None = None  # 有填就順便檢查（跑測試題用）


class TryOut(BaseModel):
    ok: bool
    output: str
    model: str | None
    elapsed_ms: int
    attempts: int
    warnings: list[str]
    retry_reasons: list[str]
    error: str | None = None
    failures: list[str] = []  # 測試題沒通過的條件
    attachments: list[dict] = []  # 工具產生的圖片
    tool_args: dict | None = None  # 工具型村民整理出的參數


def _ctx(request: Request):
    return request.app.state.ctx


def parse_spec(data: dict) -> AgentSpec:
    """驗證村民定義，錯誤訊息整理成「欄位：原因」方便在表單上看。"""
    try:
        spec = AgentSpec.model_validate(data)
    except ValidationError as e:
        msgs = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "（整體）"
            msgs.append(f"{loc}：{err['msg'].removeprefix('Value error, ')}")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "；".join(msgs)) from None
    if spec.id in RESERVED_IDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"card.id：「{spec.id}」是保留名稱")
    return spec


async def _changed(request: Request) -> None:
    """名冊有變動：重新載入並通知前端（村民出現 / 消失 / 換裝）。"""
    ctx = _ctx(request)
    await load_registry(ctx.sessionmaker, ctx.registry)
    await ctx.bus.publish("agents.changed", agents=[a.id for a in ctx.registry.all()], errors=ctx.registry.errors)


async def _get(s, villager_id: str) -> AgentDef:
    row = await s.get(AgentDef, villager_id)
    if row is None or row.deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"找不到村民「{villager_id}」")
    return row


@router.get("")
async def list_villagers(request: Request) -> list[VillagerOut]:
    async with _ctx(request).sessionmaker() as s:
        rows = await s.scalars(select(AgentDef).where(~AgentDef.deleted).order_by(AgentDef.created_at))
        return [VillagerOut.of(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_villager(body: SpecBody, request: Request) -> VillagerOut:
    spec = parse_spec(body.spec)
    async with _ctx(request).sessionmaker() as s:
        row = await s.get(AgentDef, spec.id)
        if row is not None and not row.deleted:
            raise HTTPException(status.HTTP_409_CONFLICT, f"card.id：「{spec.id}」已經有人用了")
        if row is None:
            row = AgentDef(id=spec.id, spec=spec.to_doc())
            s.add(row)
        else:  # 刪掉的內建範例 id 被重新使用
            row.spec, row.deleted, row.enabled, row.builtin = spec.to_doc(), False, True, False
        await s.commit()
        await s.refresh(row)
    await _changed(request)
    return VillagerOut.of(row)


@router.put("/{villager_id}")
async def update_villager(villager_id: str, body: SpecBody, request: Request) -> VillagerOut:
    spec = parse_spec(body.spec)
    if spec.id != villager_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "card.id：建立後不能修改（可以用「複製」建立新村民）")
    async with _ctx(request).sessionmaker() as s:
        row = await _get(s, villager_id)
        row.spec = spec.to_doc()
        await s.commit()
        await s.refresh(row)
    await _changed(request)
    return VillagerOut.of(row)


@router.patch("/{villager_id}")
async def set_enabled(villager_id: str, body: EnabledBody, request: Request) -> VillagerOut:
    async with _ctx(request).sessionmaker() as s:
        row = await _get(s, villager_id)
        row.enabled = body.enabled
        await s.commit()
        await s.refresh(row)
    await _changed(request)
    return VillagerOut.of(row)


@router.delete("/{villager_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_villager(villager_id: str, request: Request) -> None:
    async with _ctx(request).sessionmaker() as s:
        row = await _get(s, villager_id)
        if row.builtin:
            row.deleted = True  # 留下紀錄，重啟時才不會又被匯入
        else:
            await s.delete(row)
        await s.commit()
    await _changed(request)


@router.post("/restore-builtin")
async def restore_builtin(request: Request) -> dict:
    ctx = _ctx(request)
    restored = await seed_builtin(ctx.sessionmaker, ctx.settings.agents_dir, restore=True)
    await _changed(request)
    return {"restored": restored}


@router.post("/to-yaml")
async def to_yaml(body: SpecBody) -> dict:
    doc = parse_spec(body.spec).to_doc()
    return {"yaml": yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=1000)}


@router.post("/from-yaml")
async def from_yaml(body: YamlBody) -> dict:
    """解析 YAML 並驗證，回傳 spec 讓前端載入編輯器（不會直接儲存）。"""
    try:
        data = yaml.safe_load(body.yaml)
    except yaml.YAMLError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"YAML 格式錯誤：{e}") from None
    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "YAML 最外層必須是物件（card、prompt…）")
    return {"spec": parse_spec(data).to_doc()}


@router.post("/try")
async def try_villager(body: TryBody, request: Request) -> TryOut:
    """用（可能還沒儲存的）設定跑一次，流程與問答時相同：LLM → harness 檢查 → 不合格重做。"""
    spec = parse_spec(body.spec)
    r = StepResult(id="try", agent=spec.id, instruction=body.instruction, use=[])
    reasons: list[str] = []

    async def on_retry(reason: str) -> None:
        reasons.append(reason)

    t0 = time.monotonic()
    error = None
    try:
        await run_agent(
            _ctx(request).llm, spec, body.instruction, body.context, r, on_retry=on_retry, target=body.target
        )
    except LLMError as e:
        error = str(e)
    failures = check_expect(r.output, body.expect) if body.expect and error is None else []
    return TryOut(
        ok=error is None and not failures,
        output=r.output,
        model=r.model,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        attempts=r.attempts,
        warnings=r.warnings,
        retry_reasons=reasons,
        error=error,
        failures=failures,
        attachments=r.attachments,
        tool_args=r.tool_args,
    )


@router.get("/tools")
async def list_tools() -> list[dict]:
    """工具箱：工具型村民可以使用的工具（名稱、說明、預設的參數整理說明、建議關鍵字）。"""
    return [t.info() for t in TOOLS.values()]
