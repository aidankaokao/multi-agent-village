"""回答用的 LLM：由呼叫端每次帶進來（網址、模型、API key），agent 不存任何 LLM 的 key。

- 網址只能是允許清單裡的主機（避免 agent 被拿去連任意網址、探測內網）
- key 只在這次呼叫用，不寫 log、不出現在錯誤訊息、不回傳
- 走 OpenAI 相容的 /chat/completions（OpenRouter、OpenAI、Ollama 都可以）
- 要 JSON 時送 response_format（json_schema）；模型不收就拿掉約束，照樣從回答裡找出 JSON
"""

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger("np.llm")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    base_url: str  # 例如 https://openrouter.ai/api/v1
    model: str
    api_key: str = ""
    reasoning_effort: str | None = None  # 思考型模型可以指定（none / low / medium / high）

    def __repr__(self) -> str:  # 不讓 key 出現在任何 repr / log
        return f"LLMConfig(base_url={self.base_url!r}, model={self.model!r}, api_key={'***' if self.api_key else ''})"


def check_url(base_url: str, allowed_hosts: list[str]) -> None:
    u = urlparse(base_url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise LLMError(f"LLM 網址格式不對：{base_url}")
    if u.hostname not in allowed_hosts:
        raise LLMError(f"不允許的 LLM 主機：{u.hostname}（允許：{', '.join(allowed_hosts)}；可在 .env 的 NP_ALLOWED_LLM_HOSTS 調整）")


def _client(**kw) -> httpx.AsyncClient:  # 測試會替換這個
    return httpx.AsyncClient(**kw)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _body(cfg: LLMConfig, system: str, user: str, max_tokens: int, schema: dict | None = None) -> dict:
    body: dict = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if cfg.reasoning_effort:
        body["reasoning_effort"] = cfg.reasoning_effort
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "report", "schema": schema}}
    return body


def _adapt(body: dict, status: int, text: str) -> bool:
    """有些模型不收某些參數（gpt-5 不收 max_tokens、非思考型不收 reasoning_effort）：調整後回傳 True 重試。"""
    if status != 400:
        return False
    if "max_tokens" in text and "max_tokens" in body:
        body["max_completion_tokens"] = body.pop("max_tokens")
        return True
    if "reasoning_effort" in text and "reasoning_effort" in body:
        body.pop("reasoning_effort")
        return True
    if "response_format" in body and any(w in text for w in ("response_format", "json_schema", "schema")):
        body.pop("response_format")  # 不支援結構化輸出：不送約束，照樣使用
        return True
    return False


def _headers(cfg: LLMConfig) -> dict:
    return {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}


async def chat(
    cfg: LLMConfig, system: str, user: str, *, max_tokens: int = 1500, timeout: float = 240.0, schema: dict | None = None,
) -> tuple[str, str]:
    """一次拿到完整回答。回傳 (回答文字, 實際使用的模型名稱)。"""
    body = _body(cfg, system, user, max_tokens, schema)
    async with _client(base_url=cfg.base_url.rstrip("/") + "/", headers=_headers(cfg), timeout=timeout) as http:
        for _ in range(4):
            try:
                r = await http.post("chat/completions", json=body)
            except httpx.HTTPError as e:
                raise LLMError(_redact(f"連不上 LLM：{type(e).__name__}: {e}", cfg.api_key)) from None
            if not _adapt(body, r.status_code, r.text):
                break
    if r.status_code >= 400:
        raise LLMError(_redact(f"LLM 回傳錯誤 HTTP {r.status_code}：{r.text[:400]}", cfg.api_key))
    try:
        data = r.json()
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError(f"LLM 回應格式不對：{r.text[:200]}") from None
    if not text.strip():
        raise LLMError("LLM 沒有回答內容（思考型模型可以把 reasoning_effort 設低一點）")
    if choice.get("finish_reason") == "length" and schema:
        raise LLMError("LLM 的回答太長被截斷（JSON 不完整）")
    return text.strip(), data.get("model") or cfg.model


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(text: str) -> dict:
    """從回答裡取出 JSON 物件（容許外面包 ```json、前後多了說明文字）。"""
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for c in candidates:
        start, end = c.find("{"), c.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            obj = json.loads(c[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise LLMError("LLM 的回答不是有效的 JSON")
