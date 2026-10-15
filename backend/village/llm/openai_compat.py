"""OpenAI 相容 API client：Ollama、vLLM、LM Studio、OpenAI 及多數雲端都適用。"""

import json

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
        self, model, messages, *, system=None, max_tokens=2048, temperature=None, json_schema=None,
        reasoning_effort=None, on_delta=None,
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
        if on_delta is not None:
            text, finish, model_name = await self._stream(body, on_delta)
            usage: dict = {}
        else:
            data = await self._request("POST", "chat/completions", json=body)
            try:
                choice = data["choices"][0]
                text = choice["message"]["content"] or ""
            except (KeyError, IndexError) as e:
                raise LLMError(f"unexpected response shape: {e}") from None
            finish, model_name, usage = choice.get("finish_reason"), data.get("model", model), data.get("usage") or {}
        if not text.strip() and finish == "length":
            raise LLMError(
                f"輸出在 {max_tokens} tokens 被截斷且沒有內容（思考型模型？可在 LLM 設定把思考強度設為 none）"
            )
        return ChatResult(text=text, model=model_name or model, usage=usage)

    async def _stream(self, body: dict, on_delta) -> tuple[str, str | None, str | None]:
        """SSE 串流：每行 `data: {...}`，以 `data: [DONE]` 結束。"""
        parts: list[str] = []
        finish = model_name = None
        try:
            async with self._http.stream("POST", "chat/completions", json={**body, "stream": True}) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode(errors="replace")[:500]
                    raise LLMError(redact(f"HTTP {resp.status_code}: {detail}"))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if err := chunk.get("error"):
                        raise LLMError(redact(f"stream error: {err}"))
                    model_name = chunk.get("model") or model_name
                    for choice in chunk.get("choices") or []:
                        if piece := (choice.get("delta") or {}).get("content"):
                            parts.append(piece)
                            await on_delta(piece)
                        finish = choice.get("finish_reason") or finish
        except httpx.HTTPError as e:
            raise LLMError(redact(f"{type(e).__name__}: {e}")) from None
        return "".join(parts), finish, model_name

    async def list_models(self) -> list[RemoteModel]:
        data = await self._request("GET", "models")
        return [RemoteModel(id=m["id"]) for m in data.get("data", [])]

    async def aclose(self) -> None:
        await self._http.aclose()
