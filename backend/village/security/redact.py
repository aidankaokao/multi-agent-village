"""把可能的機密從文字中遮掉（log、錯誤訊息、trace 共用）。

兩道防線：
1. 已知明文：解密過的 key 會登記在這裡，出現就整段替換
2. 常見格式：sk-...、Bearer token、x-api-key 等樣式
"""

import logging
import re

_known: set[str] = set()

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(x-api-key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-]{8,}"),
]


def register_secret(secret: str) -> None:
    if secret and len(secret) >= 8:
        _known.add(secret)


def redact(text: str) -> str:
    for s in _known:
        text = text.replace(s, "[REDACTED]")
    for p in _PATTERNS:
        text = p.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # 逐一遮罩 msg 與 args，保留 args 結構（uvicorn access formatter 依賴它）
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True


def install_log_redaction() -> None:
    f = RedactingFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(f)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "village"):
        logging.getLogger(name).addFilter(f)
