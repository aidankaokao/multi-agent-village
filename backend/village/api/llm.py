"""LLM 設定 API：provider / 模型註冊、模型分級、試打、看圖測驗。

每個帳號各自管理自己的 LLM（自己的 API key 與額度），只看得到、改得到自己的；
用別人的 id 一律當作不存在（404），不透露是否存在。
API key 只寫不讀：回應只有 has_api_key 與 api_key_hint。
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from village.llm.base import DEFAULT_BASE_URLS, ChatMessage, LLMError, ProviderKind, ReasoningEffort
from village.llm import vision_test
from village.llm.service import ALL_TIERS, VISION, LLMService, llm_user
from village.security.auth import CurrentUser, require_user
from village.security.crypto import SecretBox, secret_hint
from village.store.models import LlmModel, LlmProvider, ModelTier, new_id

router = APIRouter(prefix="/api/llm", tags=["llm"])
Me = Depends(require_user)


# ---------------------------------------------------------------- schemas


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    base_url: str | None = None
    api_key: SecretStr | None = None  # SecretStr：repr / log 時不會露出明文


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = None
    api_key: SecretStr | None = None  # 不傳 = 保留原值
    clear_api_key: bool = False
    enabled: bool | None = None


class ProviderOut(BaseModel):
    id: str
    name: str
    kind: str
    base_url: str | None
    enabled: bool
    has_api_key: bool
    api_key_hint: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, p: LlmProvider) -> "ProviderOut":
        return cls(
            id=p.id,
            name=p.name,
            kind=p.kind,
            base_url=p.base_url,
            enabled=p.enabled,
            has_api_key=p.api_key_ct is not None,
            api_key_hint=p.api_key_hint,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class RemoteModelOut(BaseModel):
    id: str
    display_name: str | None
    context_window: int | None
    registered: bool


class ModelCreate(BaseModel):
    provider_id: str
    model: str = Field(min_length=1, max_length=200)
    display_name: str | None = None
    supports_json_schema: bool = True
    context_window: int | None = None
    reasoning_effort: ReasoningEffort | None = None


class ModelUpdate(BaseModel):
    display_name: str | None = None
    supports_json_schema: bool | None = None
    context_window: int | None = None
    reasoning_effort: ReasoningEffort | None = None  # 傳 null = 改回 provider 預設
    enabled: bool | None = None


class ModelOut(BaseModel):
    id: str
    provider_id: str
    provider_name: str
    model: str
    display_name: str | None
    supports_json_schema: bool
    context_window: int | None
    reasoning_effort: str | None
    vision_verified_at: datetime | None
    enabled: bool


class TiersBody(BaseModel):
    large: list[str] = []
    small: list[str] = []
    vision: list[str] = []  # 只能放通過看圖測驗的模型


class VisionTestOut(BaseModel):
    passed: bool
    reason: str
    answer: str
    expected_number: str
    expected_color: str
    image: str  # data URL，給畫面顯示測驗圖
    model: ModelOut


class TryBody(BaseModel):
    target: str = "tier:large"  # tier:large / tier:small / 模型 id
    prompt: str = Field(min_length=1, max_length=2000)


class TryOut(BaseModel):
    text: str
    model: str


# ---------------------------------------------------------------- helpers


def _sm(request: Request):
    return request.app.state.ctx.sessionmaker


def _box(request: Request) -> SecretBox:
    return request.app.state.ctx.box


def _llm(request: Request) -> LLMService:
    return request.app.state.ctx.llm


def _set_api_key(p: LlmProvider, box: SecretBox, api_key: str) -> None:
    enc = box.encrypt(api_key, p.api_key_aad)
    p.api_key_ct, p.api_key_nonce, p.key_version = enc.ciphertext, enc.nonce, enc.key_version
    p.api_key_hint = secret_hint(api_key)


def _clear_api_key(p: LlmProvider) -> None:
    p.api_key_ct = p.api_key_nonce = p.key_version = p.api_key_hint = None


async def _own_provider(s, provider_id: str, me: CurrentUser) -> LlmProvider:
    p = await s.get(LlmProvider, provider_id)
    if p is None or p.user_id != me.id:
        raise HTTPException(404, "provider not found")
    return p


async def _own_model(s, model_id: str, me: CurrentUser) -> LlmModel:
    m = await s.get(LlmModel, model_id, options=[selectinload(LlmModel.provider)])
    if m is None or m.provider.user_id != me.id:
        raise HTTPException(404, "model not found")
    return m


def _model_out(m: LlmModel) -> ModelOut:
    return ModelOut(
        id=m.id,
        provider_id=m.provider_id,
        provider_name=m.provider.name,
        model=m.model,
        display_name=m.display_name,
        supports_json_schema=m.supports_json_schema,
        context_window=m.context_window,
        reasoning_effort=m.reasoning_effort,
        vision_verified_at=m.vision_verified_at,
        enabled=m.enabled,
    )


# ---------------------------------------------------------------- providers


@router.get("/defaults", dependencies=[Me])
async def defaults():
    """新增 provider 表單的預設值（依部署環境而不同，例如容器內的 Ollama 位址）。"""
    return {"base_urls": DEFAULT_BASE_URLS}


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(request: Request, me: CurrentUser = Me):
    async with _sm(request)() as s:
        rows = await s.execute(select(LlmProvider).where(LlmProvider.user_id == me.id).order_by(LlmProvider.created_at))
        return [ProviderOut.of(p) for p in rows.scalars()]


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(body: ProviderCreate, request: Request, me: CurrentUser = Me):
    if body.kind == "openai_compat" and not body.base_url:
        raise HTTPException(422, "openai_compat 需要 base_url")
    # 先產生 id 並設定擁有者：加密時 AAD 需要它們
    p = LlmProvider(id=new_id(), user_id=me.id, name=body.name, kind=body.kind, base_url=body.base_url or None)
    if body.api_key and body.api_key.get_secret_value():
        _set_api_key(p, _box(request), body.api_key.get_secret_value())
    async with _sm(request)() as s:
        s.add(p)
        try:
            await s.commit()
        except IntegrityError:
            raise HTTPException(409, f"provider 名稱已存在：{body.name}") from None
        return ProviderOut.of(p)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: str, body: ProviderUpdate, request: Request, me: CurrentUser = Me):
    async with _sm(request)() as s:
        p = await _own_provider(s, provider_id, me)
        if body.name is not None:
            p.name = body.name
        if body.base_url is not None:
            p.base_url = body.base_url or None
        if body.enabled is not None:
            p.enabled = body.enabled
        if body.clear_api_key:
            _clear_api_key(p)
        elif body.api_key and body.api_key.get_secret_value():
            _set_api_key(p, _box(request), body.api_key.get_secret_value())
        try:
            await s.commit()
        except IntegrityError:
            raise HTTPException(409, "provider 名稱已存在") from None
        return ProviderOut.of(p)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: str, request: Request, me: CurrentUser = Me):
    async with _sm(request)() as s:
        p = await _own_provider(s, provider_id, me)
        await s.delete(p)
        await s.commit()


@router.get("/providers/{provider_id}/remote-models", response_model=list[RemoteModelOut])
async def remote_models(provider_id: str, request: Request, me: CurrentUser = Me):
    """測試連線：向 provider 查詢可用模型，並標示哪些已註冊。"""
    async with _sm(request)() as s:
        p = await _own_provider(s, provider_id, me)
        registered = set(
            (await s.execute(select(LlmModel.model).where(LlmModel.provider_id == p.id))).scalars()
        )
    client = _llm(request).client_for(p)
    try:
        models = await client.list_models()
    except LLMError as e:
        raise HTTPException(502, f"連線失敗：{e}") from None
    finally:
        await client.aclose()
    return [
        RemoteModelOut(id=m.id, display_name=m.display_name, context_window=m.context_window, registered=m.id in registered)
        for m in sorted(models, key=lambda m: m.id)
    ]


# ---------------------------------------------------------------- models


@router.get("/models", response_model=list[ModelOut])
async def list_models(request: Request, me: CurrentUser = Me):
    async with _sm(request)() as s:
        rows = await s.execute(
            select(LlmModel).join(LlmProvider).where(LlmProvider.user_id == me.id)
            .options(selectinload(LlmModel.provider)).order_by(LlmModel.created_at)
        )
        return [_model_out(m) for m in rows.scalars()]


@router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(body: ModelCreate, request: Request, me: CurrentUser = Me):
    async with _sm(request)() as s:
        p = await _own_provider(s, body.provider_id, me)
        m = LlmModel(**body.model_dump())
        m.provider = p
        s.add(m)
        try:
            await s.commit()
        except IntegrityError:
            raise HTTPException(409, "此模型已註冊") from None
        return _model_out(m)


@router.patch("/models/{model_id}", response_model=ModelOut)
async def update_model(model_id: str, body: ModelUpdate, request: Request, me: CurrentUser = Me):
    async with _sm(request)() as s:
        m = await _own_model(s, model_id, me)
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(m, k, v)
        await s.commit()
        return _model_out(m)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(model_id: str, request: Request, me: CurrentUser = Me):
    async with _sm(request)() as s:
        m = await _own_model(s, model_id, me)
        await s.delete(m)
        await s.commit()


# ---------------------------------------------------------------- tiers


@router.get("/tiers", response_model=TiersBody)
async def get_tiers(request: Request, me: CurrentUser = Me):
    async with _sm(request)() as s:
        rows = await s.execute(
            select(ModelTier).where(ModelTier.user_id == me.id).order_by(ModelTier.tier, ModelTier.position)
        )
        out: dict[str, list[str]] = {t: [] for t in ALL_TIERS}
        for t in rows.scalars():
            out.setdefault(t.tier, []).append(t.model_id)
        return TiersBody(**out)


@router.put("/tiers", response_model=TiersBody)
async def put_tiers(body: TiersBody, request: Request, me: CurrentUser = Me):
    async with _sm(request)() as s:
        ids = set(body.large) | set(body.small) | set(body.vision)
        rows = (await s.execute(
            select(LlmModel).join(LlmProvider).where(LlmModel.id.in_(ids), LlmProvider.user_id == me.id)
        )).scalars()
        found = {m.id: m for m in rows}
        if missing := ids - set(found):
            raise HTTPException(422, f"unknown model ids: {sorted(missing)}")
        if unverified := [found[i].model for i in body.vision if found[i].vision_verified_at is None]:
            raise HTTPException(422, f"還沒通過看圖測驗，不能放進 vision 分級：{', '.join(unverified)}")
        await s.execute(delete(ModelTier).where(ModelTier.user_id == me.id))
        for tier in ALL_TIERS:
            for i, mid in enumerate(dict.fromkeys(getattr(body, tier))):
                s.add(ModelTier(user_id=me.id, tier=tier, position=i, model_id=mid))
        await s.commit()
    return await get_tiers(request, me)


# ---------------------------------------------------------------- try


@router.post("/try", response_model=TryOut)
async def try_model(body: TryBody, request: Request, me: CurrentUser = Me):
    try:
        with llm_user(me.id):
            r = await _llm(request).chat(body.target, [ChatMessage("user", body.prompt)], max_tokens=512)
    except LLMError as e:
        raise HTTPException(502, str(e)) from None
    return TryOut(text=r.text, model=r.model)


@router.post("/models/{model_id}/vision-test", response_model=VisionTestOut)
async def vision_test_model(model_id: str, request: Request, me: CurrentUser = Me):
    """看圖測驗：通過就記下時間（可以放進 vision 分級），沒通過就清掉（vision 分級會略過它）。"""
    async with _sm(request)() as s:
        m = await _own_model(s, model_id, me)
        if not (m.enabled and m.provider.enabled):
            raise HTTPException(409, "這個模型（或它的供應商）已停用，先啟用再測驗")
    quiz = vision_test.make_quiz()
    try:
        with llm_user(me.id):
            r = await _llm(request).chat(
                model_id, [ChatMessage("user", vision_test.PROMPT, images=[quiz.image])], max_tokens=1024, temperature=0
            )
        answer = r.text.strip()
        result = vision_test.grade(quiz, answer)
    except LLMError as e:
        # 不收圖片的模型通常會在這裡報錯（400），也算沒通過
        answer = f"（呼叫失敗）{e}"
        result = vision_test.QuizResult(False, False, False, "模型回報錯誤：可能不支援圖片輸入")
    async with _sm(request)() as s:
        m = await _own_model(s, model_id, me)
        m.vision_verified_at = datetime.now(UTC) if result.passed else None
        await s.commit()
        out = _model_out(m)
    return VisionTestOut(
        passed=result.passed, reason=result.reason, answer=answer, expected_number=quiz.number,
        expected_color=quiz.color, image=quiz.image.data_url, model=out,
    )
