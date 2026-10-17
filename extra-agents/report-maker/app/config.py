"""設定：全部從環境變數（.env）讀取。報告用的 LLM 由呼叫端每次帶進來，不存在這裡。"""

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
    """上傳檔當不可信資料：份數、大小、列數、頁數都有上限。"""

    max_files: int = 3
    max_file_bytes: int = 10 * MB
    max_total_bytes: int = 20 * MB
    max_rows: int = 50_000  # 每個工作表 / CSV 最多讀幾列（超過的不讀，報告會註明）
    max_sheets: int = 10  # 每個 Excel 最多讀幾個工作表
    max_pages: int = 100  # PDF 最多讀幾頁
    max_unzipped_bytes: int = 200 * MB  # xlsx 解壓後的大小上限（防壓縮炸彈）


@dataclass
class Settings:
    allowed_llm_hosts: list[str] = field(default_factory=lambda: _list("RM_ALLOWED_LLM_HOSTS", DEFAULT_ALLOWED_LLM_HOSTS))
    # 存取 token：呼叫 /report、A2A 時要帶（Authorization: Bearer …）；可以設多個（逗號分隔），沒設定時一律拒絕
    access_tokens: list[str] = field(default_factory=lambda: _list("RM_ACCESS_TOKENS", []))
    # 對外的網址（寫進 agent card，讓呼叫端知道要打哪裡）
    public_url: str = field(default_factory=lambda: os.environ.get("RM_PUBLIC_URL", "http://localhost:8101"))
    # 每份文件交給 LLM 的字數上限（超過的先分段摘要）；本地小模型的 context 小，可以調低
    doc_chars: int = field(default_factory=lambda: _int("RM_DOC_CHARS", 8000))
    limits: Limits = field(
        default_factory=lambda: Limits(
            max_files=_int("RM_MAX_FILES", 3),
            max_file_bytes=_int("RM_MAX_FILE_MB", 10) * MB,
            max_total_bytes=_int("RM_MAX_TOTAL_MB", 20) * MB,
            max_rows=_int("RM_MAX_ROWS", 50_000),
            max_pages=_int("RM_MAX_PAGES", 100),
        )
    )
