"""A2A（Agent2Agent）介面：讓其他系統（例如 Multi-Agent Village）用標準格式呼叫這個 agent。

- GET  /.well-known/agent-card.json   自我介紹（名稱、技能、收哪些檔案、需要哪些 header）；公開
- POST /a2a                            JSON-RPC 2.0；需要存取 token
    - message/send     一次回覆完整的 Message（text：短版報告；file：PDF；data：統計、模型、耗時）
    - message/stream   SSE 串流：Task（working）→ status-update（進度文字，例如「讀取檔案 2/3」）…
                       → artifact-update（text）→ artifact-update（file：PDF）→ artifact-update（data，lastChunk）
                       → status-update（completed，final）

文件放在訊息的 file part：{"kind": "file", "file": {"name": "a.xlsx", "mimeType": "…", "bytes": "<base64>"}}；
不支援 uri（不去別處下載檔案）。報告用的 LLM 一樣放在 HTTP header（X-LLM-Base-URL、X-LLM-Model、X-LLM-API-Key、
X-LLM-Reasoning-Effort），不放在訊息內容裡。
"""

import base64
import binascii
import uuid

from app.config import Settings
from app.readers import KIND_LABEL, SUPPORTED

PROTOCOL_VERSION = "0.3.0"
PDF_MIME = "application/pdf"
INPUT_MIMES = [
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/plain",
    PDF_MIME,
]
MAX_QUESTION = 2000


def agent_card(settings: Settings) -> dict:
    lim = settings.limits
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "文件彙整師",
        "description": (
            f"把上傳的文件（Excel、CSV、txt、PDF，最多 {lim.max_files} 份）整理成一份彙整報告（PDF 可下載）："
            "總結、重點數字、各檔重點、跨檔比較、結論與建議；表格的總和、平均、分組小計由程式計算。"
            "需要附上文件；掃描版 PDF 不支援。"
        ),
        "url": settings.public_url.rstrip("/") + "/a2a",
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": INPUT_MIMES,
        "defaultOutputModes": ["text/plain", PDF_MIME, "application/json"],
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "security": [{"bearer": []}],
        "skills": [
            {
                "id": "document_report",
                "name": "文件彙整報告",
                "description": "讀取附上的文件，整理成彙整報告 PDF；可以指定重點方向（例如「著重成本」「比較三份的差異」）",
                "tags": ["文件", "報告", "Excel", "PDF", "彙整"],
                "examples": ["幫我把這三份文件整理成報告", "整理這份 Excel 的銷售狀況，著重各部門比較"],
                "inputModes": INPUT_MIMES,
                "outputModes": ["text/plain", PDF_MIME],
            }
        ],
        # 這個 agent 的擴充說明：上傳限制、報告用的 LLM 由呼叫端提供
        "x-file-limits": {
            "extensions": list(SUPPORTED), "kinds": list(KIND_LABEL.values()), "maxFiles": lim.max_files,
            "maxFileBytes": lim.max_file_bytes, "maxTotalBytes": lim.max_total_bytes,
        },
        "x-llm-headers": {
            "X-LLM-Base-URL": "OpenAI 相容 API 的網址（必須在 agent 的允許清單裡），例如 https://openrouter.ai/api/v1",
            "X-LLM-Model": "模型名稱",
            "X-LLM-API-Key": "API key（用完就丟，不保存）",
            "X-LLM-Reasoning-Effort": "選填：none / low / medium / high",
        },
    }


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def request_from(params: dict) -> tuple[str, list[tuple[str, bytes]]]:
    """從 params.message 取出（要求文字, [(檔名, 內容)…]）。"""
    msg = params.get("message") if isinstance(params, dict) else None
    if not isinstance(msg, dict):
        raise RpcError(-32602, "params.message 是必填的")
    texts: list[str] = []
    files: list[tuple[str, bytes]] = []
    for p in msg.get("parts") or []:
        if not isinstance(p, dict):
            continue
        if p.get("kind") == "text":
            texts.append(str(p.get("text") or ""))
        elif p.get("kind") == "file":
            f = p.get("file") or {}
            name = str(f.get("name") or "").strip() or f"檔案{len(files) + 1}"
            if "bytes" not in f:
                raise RpcError(-32602, f"「{name}」沒有內容：檔案要放在 file.bytes（base64），不支援 uri")
            try:
                files.append((name.replace("/", "_").replace("\\", "_"), base64.b64decode(f["bytes"], validate=True)))
            except (binascii.Error, TypeError, ValueError):
                raise RpcError(-32602, f"「{name}」的 base64 內容不正確") from None
    question = "\n".join(t for t in texts if t).strip()
    if len(question) > MAX_QUESTION:
        raise RpcError(-32602, f"要求太長（上限 {MAX_QUESTION} 字）")
    if not files:
        raise RpcError(-32602, "沒有收到文件：請在訊息裡附上檔案（kind=file）")
    return question, files


def pdf_name(title: str) -> str:
    safe = "".join(c for c in title if c not in '\\/:*?"<>|').strip() or "彙整報告"
    return f"{safe[:40]}.pdf"


def report_data(report: dict) -> dict:
    keep = ("title", "model", "llm_error", "notes", "timings", "failed", "key_figures", "charts", "generated_at")
    return {**{k: report[k] for k in keep}, "documents": [
        {k: d[k] for k in ("name", "kind", "size", "pages", "info", "notes")} for d in report["documents"]
    ]}


def parts(report: dict, markdown: str, pdf: bytes) -> list[dict]:
    return [
        {"kind": "text", "text": markdown},
        {"kind": "file", "file": {"name": pdf_name(report["title"]), "mimeType": PDF_MIME,
                                   "bytes": base64.b64encode(pdf).decode()}},
        {"kind": "data", "data": report_data(report)},
    ]


def reply(report: dict, markdown: str, pdf: bytes, context_id: str | None) -> dict:
    return {
        "kind": "message",
        "role": "agent",
        "messageId": uuid.uuid4().hex,
        "contextId": context_id or uuid.uuid4().hex,
        "parts": parts(report, markdown, pdf),
    }


def stream_events(task_id: str, context_id: str):
    """產生串流事件（JSON-RPC result 的內容）的小工具。"""

    def task() -> dict:
        return {"kind": "task", "id": task_id, "contextId": context_id, "status": {"state": "working"}}

    def status(text: str) -> dict:
        return {"kind": "status-update", "taskId": task_id, "contextId": context_id, "final": False,
                "status": {"state": "working", "message": {
                    "kind": "message", "role": "agent", "messageId": uuid.uuid4().hex,
                    "parts": [{"kind": "text", "text": text}]}}}

    def artifact(artifact_id: str, part: dict, last: bool = False) -> dict:
        return {"kind": "artifact-update", "taskId": task_id, "contextId": context_id, "append": False, "lastChunk": last,
                "artifact": {"artifactId": artifact_id, "parts": [part]}}

    def done(state: str = "completed") -> dict:
        return {"kind": "status-update", "taskId": task_id, "contextId": context_id, "status": {"state": state}, "final": True}

    return task, status, artifact, done
