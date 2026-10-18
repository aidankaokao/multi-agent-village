"""A2A（Agent2Agent）介面：讓其他系統（例如 Multi-Agent Village）用標準格式呼叫報社。

- GET  /.well-known/agent-card.json   自我介紹（名稱、技能、收哪些檔案、需要哪些 header）；公開
- POST /a2a                            JSON-RPC 2.0；需要存取 token
    - message/send     一次回覆完整的 Message（text：本期要聞；file：報紙 PDF；file：第一版預覽 PNG；data：來源、模型）
    - message/stream   SSE 串流：Task（working）→ status-update（進度）… → artifact-update ×4 → status-update（completed）

採訪資料放在訊息的 file part（base64，txt / md）；不支援 uri。寫稿用的 LLM 放在 HTTP header
（X-LLM-Base-URL、X-LLM-Model、X-LLM-API-Key、X-LLM-Reasoning-Effort），不放在訊息內容裡。
"""

import base64
import binascii
import uuid

from app.config import Settings
from app.material import SUPPORTED

PROTOCOL_VERSION = "0.3.0"
PDF_MIME = "application/pdf"
PNG_MIME = "image/png"
INPUT_MIMES = ["text/plain", "text/markdown"]
MAX_QUESTION = 2000


def agent_card(settings: Settings) -> dict:
    lim = settings.limits
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "報社",
        "description": (
            f"把採訪資料（網路研究員查好的新聞、整理好的文字，txt / md，最多 {lim.max_files} 份）編成一份報紙（A4 PDF 可下載，"
            "附第一版預覽圖）：頭條、分版新聞、快訊、編輯室報告，每則附資料來源。自己不上網找資料："
            "主題要先交給網路研究員查（好幾個主題就每個主題各查一次），再把成果交給報社。適合「做成報紙、早報、日報、新聞快報」。"
        ),
        "url": settings.public_url.rstrip("/") + "/a2a",
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": INPUT_MIMES,
        "defaultOutputModes": ["text/plain", PDF_MIME, PNG_MIME, "application/json"],
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "security": [{"bearer": []}],
        "skills": [
            {
                "id": "newspaper",
                "name": "編報紙",
                "description": "把採訪資料編成報紙 PDF；可以指定主題與版面（例如「科技與財經早報」「只要頭條和快訊」）",
                "tags": ["報紙", "新聞", "早報", "PDF"],
                "examples": ["查最近的 AI 新聞和台股動態，做成一份早報", "把這些新聞整理成報紙"],
                "inputModes": INPUT_MIMES,
                "outputModes": [PDF_MIME, PNG_MIME],
            }
        ],
        "x-file-limits": {
            "extensions": list(SUPPORTED), "kinds": list(SUPPORTED.values()), "maxFiles": lim.max_files,
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
            name = str(f.get("name") or "").strip() or f"資料{len(files) + 1}.txt"
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
        raise RpcError(-32602, "沒有收到採訪資料：請附上 txt / md 檔（例如網路研究員查好的新聞），報社自己不上網找資料")
    return question, files


def file_name(paper: dict, ext: str) -> str:
    title = f"{paper['masthead']}_{paper['edition']}_{paper['date']}"
    safe = "".join(c for c in title if c not in '\\/:*?"<>|').strip()
    return f"{safe[:60]}{ext}"


def parts(paper: dict, markdown: str, pdf: bytes, png: bytes) -> list[dict]:
    return [
        {"kind": "text", "text": markdown},
        {"kind": "file", "file": {"name": file_name(paper, ".pdf"), "mimeType": PDF_MIME, "bytes": base64.b64encode(pdf).decode()}},
        {"kind": "file", "file": {"name": file_name(paper, "_第一版.png"), "mimeType": PNG_MIME, "bytes": base64.b64encode(png).decode()}},
        {"kind": "data", "data": paper},
    ]


def reply(paper: dict, markdown: str, pdf: bytes, png: bytes, context_id: str | None) -> dict:
    return {"kind": "message", "role": "agent", "messageId": uuid.uuid4().hex,
            "contextId": context_id or uuid.uuid4().hex, "parts": parts(paper, markdown, pdf, png)}


def stream_events(task_id: str, context_id: str):
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
