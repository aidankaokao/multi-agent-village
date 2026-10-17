"""從 DB 讀取 provider / 模型設定，解密 API key，建立 client，並依模型分級做備援。

LLM 設定屬於各個帳號：每次呼叫用「目前使用者」的模型與 API key（自己的額度）。
目前使用者放在 context variable（llm_user），問答開始時設定一次，之後村長、村民、審核員的呼叫都沿用；
asyncio.gather / create_task 會複製 context，平行步驟也拿得到。
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from village.llm.anthropic_client import AnthropicClient
from village.llm.base import (
    DEFAULT_BASE_URLS,
    ChatMessage,
    ChatResult,
    ImagePart,
    ImageResult,
    LLMClient,
    LLMError,
    TextCallback,
)
from village.llm.openai_compat import OpenAICompatClient
from village.security.crypto import SecretBox
from village.security.redact import register_secret
from village.store.models import LlmModel, LlmProvider, ModelTier

log = logging.getLogger("village.llm")

LLM_USER: ContextVar[str | None] = ContextVar("llm_user", default=None)
NOT_CONFIGURED = "你還沒有設定 LLM：請到「LLM 設定」註冊模型，並加入 large / small 分級"


class NoModelError(LLMError):
    """這個帳號沒有可用的模型（還沒設定，或都停用了）。"""


@contextmanager
def llm_user(user_id: str | None) -> Iterator[None]:
    """在這個區塊裡的 LLM 呼叫都用 user_id 的設定。"""
    token = LLM_USER.set(user_id)
    try:
        yield
    finally:
        LLM_USER.reset(token)

TIERS = ("large", "small")  # 一般分級：村長、村民、審核員、書記，互為備援
VISION = "vision"  # 視覺分級：只給鑑定師看圖，不和一般分級互相備援
IMAGE = "image"  # 生圖分級：只給畫家用，放的是生圖模型（不能拿來對話）
ALL_TIERS = (*TIERS, VISION, IMAGE)
NO_VISION = "你還沒有設定視覺模型：請到「LLM 設定」對模型做看圖測驗，通過後加入 vision 分級"
NO_IMAGE = "你還沒有設定生圖模型：請到「LLM 設定」註冊生圖模型並做生圖測驗，通過後加入 image 分級"

ClientFactory = Callable[[str, str | None, str | None], LLMClient]


def default_client_factory(kind: str, base_url: str | None, api_key: str | None) -> LLMClient:
    if kind == "anthropic":
        return AnthropicClient(api_key=api_key, base_url=base_url)
    url = base_url or DEFAULT_BASE_URLS.get(kind)
    if not url:
        raise LLMError(f"provider kind {kind} requires base_url")
    return OpenAICompatClient(base_url=url, api_key=api_key)


class LLMService:
    def __init__(self, sessionmaker: async_sessionmaker, box: SecretBox, client_factory: ClientFactory | None = None):
        self._sm = sessionmaker
        self._box = box
        self._factory = client_factory or default_client_factory

    def decrypt_api_key(self, p: LlmProvider) -> str | None:
        if p.api_key_ct is None or p.api_key_nonce is None or p.key_version is None:
            return None
        key = self._box.decrypt(p.api_key_ct, p.api_key_nonce, p.key_version, p.api_key_aad)
        register_secret(key)  # 之後若出現在 log / 錯誤訊息會被遮罩
        return key

    def client_for(self, p: LlmProvider) -> LLMClient:
        return self._factory(p.kind, p.base_url, self.decrypt_api_key(p))

    async def tier_models(self, tier: str, user_id: str) -> list[LlmModel]:
        async with self._sm() as s:
            rows = await s.execute(
                select(LlmModel)
                .join(ModelTier, ModelTier.model_id == LlmModel.id)
                .where(ModelTier.tier == tier, ModelTier.user_id == user_id)
                .order_by(ModelTier.position)
                .options(selectinload(LlmModel.provider))
            )
            return list(rows.scalars())

    async def candidates(self, target: str, user_id: str) -> list[LlmModel]:
        """target：'tier:large' / 'tier:small' / 'tier:vision' / 模型 id。

        兩級互為備援：large 失敗退到 small（弱模型總比沒有好）；small 沒設定或失敗則用 large。
        vision 自成一級：只用通過看圖測驗的模型，沒有就是沒有（一般模型看不到圖，備援也沒用）。
        """
        if target == f"tier:{VISION}":
            models = [m for m in await self.tier_models(VISION, user_id) if m.vision_verified_at is not None]
        elif target == f"tier:{IMAGE}":
            models = [m for m in await self.tier_models(IMAGE, user_id) if m.image_verified_at is not None]
        elif target.startswith("tier:"):
            tier = target.removeprefix("tier:")
            other = "small" if tier == "large" else "large"
            models = await self.tier_models(tier, user_id)
            models += [m for m in await self.tier_models(other, user_id) if m not in models]
        else:
            async with self._sm() as s:
                m = await s.get(LlmModel, target, options=[selectinload(LlmModel.provider)])
                models = [m] if m and m.provider.user_id == user_id else []  # 指定模型只能用自己的
        if target != f"tier:{IMAGE}" and target.startswith("tier:"):
            models = [m for m in models if m.output != "image"]  # 生圖模型不能拿來對話 / 看圖
        return [m for m in models if m.enabled and m.provider.enabled]

    async def has_models(self, user_id: str) -> bool:
        return bool(await self.candidates("tier:large", user_id))

    async def shareable(self, user_id: str | None) -> dict | None:
        """可以交給駐村專家的 LLM 設定：large 分級裡第一個 OpenAI 相容（含 Ollama）的模型。

        回傳 {base_url, model, api_key, reasoning_effort}；Anthropic 的 API 格式不同，不能交給對方。
        """
        if user_id is None:
            return None
        for m in await self.candidates("tier:large", user_id):
            if m.provider.kind in ("openai_compat", "ollama"):
                base = m.provider.base_url or DEFAULT_BASE_URLS.get(m.provider.kind)
                if base:
                    return {"base_url": base, "model": m.model, "api_key": self.decrypt_api_key(m.provider) or "",
                            "reasoning_effort": m.reasoning_effort}
        return None

    async def has_vision(self, user_id: str) -> bool:
        return bool(await self.candidates(f"tier:{VISION}", user_id))

    async def has_image(self, user_id: str) -> bool:
        return bool(await self.candidates(f"tier:{IMAGE}", user_id))

    async def generate_image(
        self, target: str, prompt: str, *, aspect_ratio: str | None = None, references: list[ImagePart] | None = None
    ) -> ImageResult:
        """生圖：依 image 分級的順序試，失敗換下一個（target 也可以是模型 id，生圖測驗用）。references = 修圖的參考圖。"""
        user_id = LLM_USER.get()
        if user_id is None:
            raise LLMError("沒有指定要用哪個帳號的 LLM")
        models = await self.candidates(target, user_id)
        if not models:
            raise NoModelError(NO_IMAGE)
        errors: list[str] = []
        for m in models:
            client = self.client_for(m.provider)
            try:
                return await client.generate_image(m.model, prompt, aspect_ratio=aspect_ratio, references=references)
            except LLMError as e:
                log.warning("image model %s failed, trying next: %s", m.model, e)
                errors.append(f"{m.model}: {e}")
            finally:
                await client.aclose()
        raise LLMError("all image models failed: " + " | ".join(errors))

    async def chat(
        self,
        target: str,
        messages: list[ChatMessage],
        *,
        on_text: TextCallback | None = None,
        fallback: str | None = None,
        **kw,
    ) -> ChatResult:
        """on_text 有值時用串流，傳入「目前為止的完整文字」；換備援模型時會從頭重新累積。

        fallback：target 找不到模型時改用（例如村民指定的模型是別人的 → 用村民設定的分級）。
        """
        user_id = LLM_USER.get()
        if user_id is None:
            raise LLMError("沒有指定要用哪個帳號的 LLM")
        models = await self.candidates(target, user_id)
        if not models and fallback:
            models = await self.candidates(fallback, user_id)
        if not models:
            raise NoModelError(NO_VISION if target == f"tier:{VISION}" else NOT_CONFIGURED)
        if any(m.output == "image" for m in models):
            raise LLMError("生圖模型不能拿來對話")
        errors: list[str] = []
        for m in models:
            call_kw = dict(kw)
            if call_kw.get("json_schema") is not None and not m.supports_json_schema:
                # 不送 schema 約束，改靠 prompt 裡的格式說明 + 上層的 JSON 解析與重試（弱模型 / 不支援的供應商也能用）
                call_kw["json_schema"] = None
            client = self.client_for(m.provider)
            on_delta = None
            if on_text is not None:
                buf: list[str] = []

                async def on_delta(piece: str, buf=buf) -> None:
                    buf.append(piece)
                    await on_text("".join(buf))

            try:
                return await client.chat(
                    m.model, messages, **{"reasoning_effort": m.reasoning_effort, **call_kw}, on_delta=on_delta
                )
            except LLMError as e:
                log.warning("model %s failed, trying next: %s", m.model, e)
                errors.append(f"{m.model}: {e}")
            finally:
                await client.aclose()
        raise LLMError("all candidates failed: " + " | ".join(errors))
