"""OpenAI 相容 API client：Ollama、vLLM、LM Studio、OpenAI 及多數雲端都適用。"""

import json
import logging
import re

import httpx

from village.llm.base import ChatMessage, ChatResult, LLMClient, LLMError, RemoteModel
from village.security.redact import redact

# 各家「OpenAI 相容」API 支援的參數不一樣（例如 OpenAI 的 gpt-5 / o 系列不收 max_tokens 與 temperature）。
# 不寫死模型名單：收到 400「參數不支援」時調整參數重試，並記住這個模型的調整，下次直接套用。
log = logging.getLogger("village.llm")

# key = (base_url, model)；client 每次呼叫都會重建，所以放模組層級。
_QUIRKS: dict[tuple[str, str], set[str]] = {}
MAX_PARAM_RETRIES = 4


class ParamError(LLMError):
    """HTTP 400 且明確指出某個參數不支援。"""

    def __init__(self, msg: str, param: str, code: str):
        super().__init__(msg)
        self.param = param
        self.code = code


_UNRECOGNIZED = re.compile(r"Unrecognized request arguments? supplied:\s*([A-Za-z_]+)")
# OpenRouter 等：一定要思考的模型（如 GLM 5.3）不能把思考關掉
_REASONING_REQUIRED = re.compile(r"reasoning is (mandatory|required)|cannot disable reasoning|reasoning.{0,40}cannot be disabled", re.I)


def _http_error(status: int, text: str) -> LLMError:
    msg = redact(f"HTTP {status}: {text[:500]}")
    if status == 400:
        try:
            err = json.loads(text).get("error") or {}
        except (json.JSONDecodeError, AttributeError):
            err = {}
        if not isinstance(err, dict):
            err = {}
        if err.get("param") and err.get("code") in ("unsupported_parameter", "unsupported_value"):
            return ParamError(msg, err["param"], err["code"])
        # 舊模型（如 gpt-4o-mini）param 是 null，參數名只寫在訊息裡
        message = str(err.get("message") or "")
        if m := _UNRECOGNIZED.search(message):
            return ParamError(msg, m.group(1), "unsupported_parameter")
        if _REASONING_REQUIRED.search(message):
            return ParamError(msg, "reasoning_effort", "reasoning_required")
    return LLMError(msg)


def _quirk_for(param: str, code: str, body: dict) -> str | None:
    """依出錯的參數決定要怎麼調整；回傳 None 代表沒辦法自動處理。"""
    if param == "max_tokens" and "max_tokens" in body:
        return "max_completion_tokens"
    if param == "temperature" and "temperature" in body:
        return "no_temperature"
    if param == "reasoning_effort" and "reasoning_effort" in body:
        # gpt-5 有這個參數但沒有 none 這個值 → 改 minimal；非思考型模型（gpt-4o-mini）根本不認得 → 拿掉
        if code == "unsupported_value" and body["reasoning_effort"] == "none":
            return "minimal_effort"
        if code == "reasoning_required":  # 一定要思考的模型：用最省的 low
            return "low_effort" if body["reasoning_effort"] in ("none", "minimal") else None
        return "no_reasoning_effort"
    if param == "stream_options" and "stream_options" in body:
        return "no_stream_usage"  # 不支援串流時回報 token 用量：拿掉，只是少了統計
    if param == "stream" and body.get("stream"):
        return "no_stream"  # 例如 OpenAI 組織未驗證時不能串流 gpt-5
    return None


def _apply(body: dict, quirks: set[str]) -> dict:
    b = dict(body)
    if "max_completion_tokens" in quirks and "max_tokens" in b:
        b["max_completion_tokens"] = b.pop("max_tokens")
    if "no_temperature" in quirks:
        b.pop("temperature", None)
    if "no_reasoning_effort" in quirks:
        b.pop("reasoning_effort", None)
    elif "low_effort" in quirks and b.get("reasoning_effort") in ("none", "minimal"):
        b["reasoning_effort"] = "low"
    elif "minimal_effort" in quirks and b.get("reasoning_effort") == "none":
        b["reasoning_effort"] = "minimal"
    if "no_stream" in quirks:
        b.pop("stream", None)
    if "no_stream_usage" in quirks or not b.get("stream"):
        b.pop("stream_options", None)
    return b


def _usage(u) -> dict:
    """統一成 {input_tokens, output_tokens}（和 Anthropic 一樣），沒有就回空 dict。"""
    if not isinstance(u, dict) or "prompt_tokens" not in u:
        return {}
    return {"input_tokens": u.get("prompt_tokens") or 0, "output_tokens": u.get("completion_tokens") or 0}


def _content(m: ChatMessage) -> str | list[dict]:
    """有圖片時改用多段內容（OpenAI / OpenRouter / Ollama 都收這個格式）；圖片放在文字前面。

    detail=high：OpenAI 預設 auto 可能用低解析度看，菜單、收據這類小字容易看錯（Ollama 會忽略這個欄位）。
    """
    if not m.images:
        return m.content
    parts: list[dict] = [{"type": "image_url", "image_url": {"url": img.data_url, "detail": "high"}} for img in m.images]
    return [*parts, {"type": "text", "text": m.content}]


class OpenAICompatClient(LLMClient):
    def __init__(self, base_url: str, api_key: str | None, timeout: float = 120.0):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self._base_url + "/", headers=headers, timeout=timeout)

    async def _request(self, method: str, path: str, **kw) -> dict:
        try:
            resp = await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise LLMError(redact(f"{type(e).__name__}: {e}")) from None
        if resp.status_code >= 400:
            raise _http_error(resp.status_code, resp.text)
        return resp.json()

    async def chat(
        self, model, messages, *, system=None, max_tokens=2048, temperature=None, json_schema=None,
        reasoning_effort=None, on_delta=None,
    ):
        msgs = [{"role": "system", "content": system}] if system else []
        msgs += [{"role": m.role, "content": _content(m)} for m in messages]
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
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}  # 串流最後一塊附上 token 用量
        quirks = _QUIRKS.setdefault((self._base_url, model), set())
        for attempt in range(MAX_PARAM_RETRIES + 1):
            try:
                text, finish, model_name, usage = await self._send(_apply(body, quirks), on_delta)
                break
            except ParamError as e:
                fix = _quirk_for(e.param, e.code, _apply(body, quirks))
                if fix is None or fix in quirks or attempt == MAX_PARAM_RETRIES:
                    raise
                log.info("%s 不支援參數 %s，改用 %s 重試", model, e.param, fix)
                quirks.add(fix)
        if json_schema is not None and finish == "length":
            # 結構化輸出被截斷（有些模型 / 供應商會在 JSON 裡一直輸出空白直到 token 用完）：
            # 半個 JSON 解析不了，直接當失敗讓上層換下一個模型，比叫同一個模型重試有用
            raise LLMError(f"JSON 輸出在 {max_tokens} tokens 被截斷（模型可能卡在輸出空白）")
        if not text.strip() and finish == "length":
            raise LLMError(
                f"輸出在 {max_tokens} tokens 被截斷且沒有內容（思考型模型？可在 LLM 設定把思考強度設為 none）"
            )
        return ChatResult(text=text, model=model_name or model, usage=usage)

    async def _send(self, body: dict, on_delta) -> tuple[str, str | None, str | None, dict]:
        if body.get("stream"):
            return await self._stream(body, on_delta)
        data = await self._request("POST", "chat/completions", json=body)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response shape: {e}") from None
        if on_delta is not None and text:  # 想串流但服務不允許：一次給完
            await on_delta(text)
        return text, choice.get("finish_reason"), data.get("model"), _usage(data.get("usage"))

    async def _stream(self, body: dict, on_delta) -> tuple[str, str | None, str | None, dict]:
        """SSE 串流：每行 `data: {...}`，以 `data: [DONE]` 結束。"""
        parts: list[str] = []
        finish = model_name = None
        usage: dict = {}
        try:
            async with self._http.stream("POST", "chat/completions", json=body) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode(errors="replace")
                    raise _http_error(resp.status_code, detail)
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
                    usage = _usage(chunk.get("usage")) or usage
                    for choice in chunk.get("choices") or []:
                        if piece := (choice.get("delta") or {}).get("content"):
                            parts.append(piece)
                            await on_delta(piece)
                        finish = choice.get("finish_reason") or finish
        except httpx.HTTPError as e:
            raise LLMError(redact(f"{type(e).__name__}: {e}")) from None
        return "".join(parts), finish, model_name, usage

    async def list_models(self) -> list[RemoteModel]:
        data = await self._request("GET", "models")
        return [RemoteModel(id=m["id"]) for m in data.get("data", [])]

    async def aclose(self) -> None:
        await self._http.aclose()
