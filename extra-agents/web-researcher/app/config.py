"""設定：全部從環境變數（.env）讀取。回答用的 LLM 由呼叫端每次帶進來，不存在這裡。"""

import os
from dataclasses import dataclass, field

# 呼叫端可以指定的 LLM 網址（避免 agent 被拿去連任意網址、探測內網）
DEFAULT_ALLOWED_LLM_HOSTS = ["openrouter.ai", "api.openai.com", "localhost", "127.0.0.1", "host.docker.internal"]


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else list(default)


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass
class Settings:
    # 搜尋引擎：同一個 docker-compose 裡的 SearXNG（不對外開放）；本機開發可以指到自己跑的 SearXNG
    searxng_url: str = field(default_factory=lambda: os.environ.get("WR_SEARXNG_URL", "http://localhost:8888"))
    language: str = field(default_factory=lambda: os.environ.get("WR_LANGUAGE", "zh-TW"))
    allowed_llm_hosts: list[str] = field(default_factory=lambda: _list("WR_ALLOWED_LLM_HOSTS", DEFAULT_ALLOWED_LLM_HOSTS))
    # 存取 token：呼叫 /ask、/search、A2A 時要帶（Authorization: Bearer …）；可以設多個（逗號分隔），沒設定時一律拒絕
    access_tokens: list[str] = field(default_factory=lambda: _list("WR_ACCESS_TOKENS", []))
    public_url: str = field(default_factory=lambda: os.environ.get("WR_PUBLIC_URL", "http://localhost:8102"))
    max_queries: int = field(default_factory=lambda: _int("WR_MAX_QUERIES", 3))  # 一個問題改寫成幾組關鍵字
    max_pages: int = field(default_factory=lambda: _int("WR_MAX_PAGES", 6))  # 最多讀幾個網頁
    per_site: int = field(default_factory=lambda: _int("WR_PER_SITE", 2))  # 同一個網站最多讀幾頁（來源多樣一點）
    page_bytes: int = field(default_factory=lambda: _int("WR_PAGE_KB", 2048) * 1024)  # 每個網頁最多下載多少
    fetch_timeout: float = field(default_factory=lambda: float(os.environ.get("WR_FETCH_TIMEOUT", "10")))
    passages: int = field(default_factory=lambda: _int("WR_PASSAGES", 10))  # 交給 LLM 的段落數
