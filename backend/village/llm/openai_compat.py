"""OpenAI 相容 API client：Ollama、vLLM、LM Studio、OpenAI 及多數雲端都適用。"""

import httpx

from village.llm.base import ChatResult, LLMClient, LLMError, RemoteModel
from village.security.redact import redact


class OpenAICompatClient(LLMClient):
    def __init__(self, base_url: str, api_key: str | None, timeout: float = 120.0):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", headers=headers, timeout=timeout)

    async def _request(self, method: str, path: str, **kw) -> dict:
        try:
            resp = await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise LLMError(redact(f"{type(e).__name__}: {e}")) from None
        if resp.status_code >= 400:
            raise LLMError(redact(f"HTTP {resp.status_code}: {resp.text[:500]}"))
        return resp.json()

    async def chat(
        self, model, messages, *, system=None, max_tokens=2048, temperature=None, json_schema=None, reasoning_effort=None
    ):
        msgs = [{"role": "system", "content": system}] if system else []
        msgs += [{"role": m.role, "content": m.content} for m in messages]
        body: dict = {"model": model, "messages": msgs, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema, "strict": True},
            }
        data = await self._request("POST", "chat/completions", json=body)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"unexpected response shape: {e}") from None
        if not text.strip() and choice.get("finish_reason") == "length":
            raise LLMError(
                f"輸出在 {max_tokens} tokens 被截斷且沒有內容（思考型模型？可在 LLM 設定把思考強度設為 none）"
            )
        return ChatResult(text=text, model=data.get("model", model), usage=data.get("usage") or {})

    async def list_models(self) -> list[RemoteModel]:
        data = await self._request("GET", "models")
        return [RemoteModel(id=m["id"]) for m in data.get("data", [])]

    async def aclose(self) -> None:
        await self._http.aclose()
