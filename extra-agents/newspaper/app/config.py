"""設定：全部從環境變數（.env）讀取。寫報紙用的 LLM 由呼叫端每次帶進來，不存在這裡。"""

import os
from dataclasses import dataclass, field

# 呼叫端可以指定的 LLM 網址（避免 agent 被拿去連任意網址、探測內網）
DEFAULT_ALLOWED_LLM_HOSTS = ["openrouter.ai", "api.openai.com", "localhost", "127.0.0.1", "host.docker.internal"]

MB = 1024 * 1024


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else list(default)


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass
class Limits:
    """收到的資料當不可信資料：份數、大小都有上限。"""

    max_files: int = 6
    max_file_bytes: int = 2 * MB
    max_total_bytes: int = 6 * MB


@dataclass
class Settings:
    allowed_llm_hosts: list[str] = field(default_factory=lambda: _list("NP_ALLOWED_LLM_HOSTS", DEFAULT_ALLOWED_LLM_HOSTS))
    # 存取 token：呼叫 A2A 時要帶（Authorization: Bearer …）；可以設多個（逗號分隔），沒設定時一律拒絕
    access_tokens: list[str] = field(default_factory=lambda: _list("NP_ACCESS_TOKENS", []))
    # 對外的網址（寫進 agent card）
    public_url: str = field(default_factory=lambda: os.environ.get("NP_PUBLIC_URL", "http://localhost:8103"))
    # 報頭（報紙名稱）
    masthead: str = field(default_factory=lambda: os.environ.get("NP_MASTHEAD", "村莊日報"))
    # 每份資料交給 LLM 的字數上限（本機小模型 context 小，可以調低）
    doc_chars: int = field(default_factory=lambda: _int("NP_DOC_CHARS", 6000))
    limits: Limits = field(
        default_factory=lambda: Limits(
            max_files=_int("NP_MAX_FILES", 6),
            max_file_bytes=_int("NP_MAX_FILE_MB", 2) * MB,
            max_total_bytes=_int("NP_MAX_TOTAL_MB", 6) * MB,
        )
    )
