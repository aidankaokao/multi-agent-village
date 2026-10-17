"""回答用的 LLM：由呼叫端每次帶進來（網址、模型、API key），agent 不存任何 LLM 的 key。

- 網址只能是允許清單裡的主機（避免 agent 被拿去連任意網址、探測內網）
- key 只在這次呼叫用，不寫 log、不出現在錯誤訊息、不回傳
- 走 OpenAI 相容的 /chat/completions（OpenRouter、OpenAI、Ollama 都可以）
"""

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger("th.llm")


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
        raise LLMError(f"不允許的 LLM 主機：{u.hostname}（允許：{', '.join(allowed_hosts)}；可在 .env 的 TH_ALLOWED_LLM_HOSTS 調整）")


def _client(**kw) -> httpx.AsyncClient:  # 測試會替換這個
    return httpx.AsyncClient(**kw)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


async def chat(cfg: LLMConfig, system: str, user: str, *, max_tokens: int = 1500, timeout: float = 180.0) -> tuple[str, str]:
    """回傳 (回答文字, 實際使用的模型名稱)。"""
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    body: dict = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if cfg.reasoning_effort:
        body["reasoning_effort"] = cfg.reasoning_effort
    async with _client(base_url=cfg.base_url.rstrip("/") + "/", headers=headers, timeout=timeout) as http:
        for _ in range(3):  # 有些模型不收某些參數：調整後重試
            try:
                r = await http.post("chat/completions", json=body)
            except httpx.HTTPError as e:
                raise LLMError(_redact(f"連不上 LLM：{type(e).__name__}: {e}", cfg.api_key)) from None
            if r.status_code == 400 and "max_tokens" in r.text and "max_tokens" in body:
                body["max_completion_tokens"] = body.pop("max_tokens")  # gpt-5 等
                continue
            if r.status_code == 400 and "reasoning_effort" in r.text and "reasoning_effort" in body:
                body.pop("reasoning_effort")
                continue
            break
    if r.status_code >= 400:
        raise LLMError(_redact(f"LLM 回傳錯誤 HTTP {r.status_code}：{r.text[:400]}", cfg.api_key))
    try:
        data = r.json()
        text = data["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError(f"LLM 回應格式不對：{r.text[:200]}") from None
    if not text.strip():
        raise LLMError("LLM 沒有回答內容（思考型模型可以把 reasoning_effort 設低一點）")
    return text.strip(), data.get("model") or cfg.model
