"""Anthropic（Claude）client，使用官方 SDK。"""

import anthropic

from village.llm.base import ChatMessage, ChatResult, LLMClient, LLMError, RemoteModel
from village.security.redact import redact


def _content(m: ChatMessage) -> str | list[dict]:
    if not m.images:
        return m.content
    parts: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": img.mime, "data": img.data}} for img in m.images
    ]
    return [*parts, {"type": "text", "text": m.content}]


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str | None, base_url: str | None = None):
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url or None)

    async def chat(
        self, model, messages, *, system=None, max_tokens=2048, temperature=None, json_schema=None,
        reasoning_effort=None, on_delta=None,
    ):
        # 新世代 Claude 模型不接受 temperature 等取樣參數，這裡刻意不傳
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": _content(m)} for m in messages],
        }
        if system:
            kwargs["system"] = system
        output_config: dict = {}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if reasoning_effort in ("low", "medium", "high"):  # Claude 沒有 none，用 low 最省
            output_config["effort"] = reasoning_effort
        elif reasoning_effort == "none":
            output_config["effort"] = "low"
        if output_config:
            kwargs["output_config"] = output_config
        try:
            if on_delta is None:
                resp = await self._client.messages.create(**kwargs)
            else:
                async with self._client.messages.stream(**kwargs) as stream:
                    async for piece in stream.text_stream:
                        await on_delta(piece)
                    resp = await stream.get_final_message()
        except anthropic.APIError as e:
            raise LLMError(redact(f"{type(e).__name__}: {e}")) from None
        if resp.stop_reason == "refusal":
            raise LLMError("model refused the request")
        text = "".join(b.text for b in resp.content if b.type == "text")
        if json_schema is not None and resp.stop_reason == "max_tokens":
            raise LLMError(f"JSON 輸出在 {max_tokens} tokens 被截斷")
        if not text.strip() and resp.stop_reason == "max_tokens":
            raise LLMError(f"輸出在 {max_tokens} tokens 被截斷且沒有內容")
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
