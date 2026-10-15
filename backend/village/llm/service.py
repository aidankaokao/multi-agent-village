"""從 DB 讀取 provider / 模型設定，解密 API key，建立 client，並依模型分級做備援。"""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from village.llm.anthropic_client import AnthropicClient
from village.llm.base import DEFAULT_BASE_URLS, ChatMessage, ChatResult, LLMClient, LLMError, TextCallback
from village.llm.openai_compat import OpenAICompatClient
from village.security.crypto import SecretBox
from village.security.redact import register_secret
from village.store.models import LlmModel, LlmProvider, ModelTier

log = logging.getLogger("village.llm")

TIERS = ("large", "small")

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

    async def tier_models(self, tier: str) -> list[LlmModel]:
        async with self._sm() as s:
            rows = await s.execute(
                select(LlmModel)
                .join(ModelTier, ModelTier.model_id == LlmModel.id)
                .where(ModelTier.tier == tier)
                .order_by(ModelTier.position)
                .options(selectinload(LlmModel.provider))
            )
            return list(rows.scalars())

    async def candidates(self, target: str) -> list[LlmModel]:
        """target：'tier:large' / 'tier:small' / 模型 id。

        兩級互為備援：large 失敗退到 small（弱模型總比沒有好）；small 沒設定或失敗則用 large。
        """
        if target.startswith("tier:"):
            tier = target.removeprefix("tier:")
            other = "small" if tier == "large" else "large"
            models = await self.tier_models(tier)
            models += [m for m in await self.tier_models(other) if m not in models]
        else:
            async with self._sm() as s:
                m = await s.get(LlmModel, target, options=[selectinload(LlmModel.provider)])
                models = [m] if m else []
        return [m for m in models if m.enabled and m.provider.enabled]

    async def chat(
        self, target: str, messages: list[ChatMessage], *, on_text: TextCallback | None = None, **kw
    ) -> ChatResult:
        """on_text 有值時用串流，傳入「目前為止的完整文字」；換備援模型時會從頭重新累積。"""
        models = await self.candidates(target)
        if not models:
            raise LLMError(f"no enabled model for {target}; register one in LLM 設定")
        errors: list[str] = []
        for m in models:
            if kw.get("json_schema") is not None and not m.supports_json_schema:
                errors.append(f"{m.model}: json_schema not supported")
                continue
            client = self.client_for(m.provider)
            on_delta = None
            if on_text is not None:
                buf: list[str] = []

                async def on_delta(piece: str, buf=buf) -> None:
                    buf.append(piece)
                    await on_text("".join(buf))

            try:
                return await client.chat(
                    m.model, messages, **{"reasoning_effort": m.reasoning_effort, **kw}, on_delta=on_delta
                )
            except LLMError as e:
                log.warning("model %s failed, trying next: %s", m.model, e)
                errors.append(f"{m.model}: {e}")
            finally:
                await client.aclose()
        raise LLMError("all candidates failed: " + " | ".join(errors))
