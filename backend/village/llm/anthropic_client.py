"""Anthropic（Claude）client，使用官方 SDK。"""

import anthropic

from village.llm.base import ChatResult, LLMClient, LLMError, RemoteModel
from village.security.redact import redact


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str | None, base_url: str | None = None):
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url or None)

    async def chat(self, model, messages, *, system=None, max_tokens=2048, temperature=None, json_schema=None):
        # 新世代 Claude 模型不接受 temperature 等取樣參數，這裡刻意不傳
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system:
            kwargs["system"] = system
        if json_schema is not None:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
        try:
            resp = await self._client.messages.create(**kwargs)
        except anthropic.APIError as e:
            raise LLMError(redact(f"{type(e).__name__}: {e}")) from None
        if resp.stop_reason == "refusal":
            raise LLMError("model refused the request")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return ChatResult(
            text=text,
            model=resp.model,
            usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
        )

    async def list_models(self) -> list[RemoteModel]:
        try:
            return [
                RemoteModel(id=m.id, display_name=m.display_name, context_window=getattr(m, "max_input_tokens", None))
                async for m in self._client.models.list()
            ]
        except anthropic.APIError as e:
            raise LLMError(redact(f"{type(e).__name__}: {e}")) from None

    async def aclose(self) -> None:
        await self._client.close()
