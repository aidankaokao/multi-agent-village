"""A2A（Agent2Agent）介面：讓其他系統（例如 Multi-Agent Village）用標準格式呼叫這個 agent。

- GET  /.well-known/agent-card.json   自我介紹；公開
- POST /a2a                            JSON-RPC 2.0；需要存取 token
    - message/send     一次回覆完整的 Message（text：回答；data：引用來源、搜尋關鍵字、模型、耗時）
    - message/stream   SSE：Task → status-update（進度：搜尋、讀網頁 3/6…）→ artifact-update（回答，一段一段 append）
                       → artifact-update（data，lastChunk）→ status-update（completed，final）

回答用的 LLM 放在 HTTP header（X-LLM-Base-URL、X-LLM-Model、X-LLM-API-Key、X-LLM-Reasoning-Effort）。
data 的 citations 格式和台灣史專家相同（n、title、section、url），村子會自動整理成「資料來源」。
"""

import uuid

from app.config import Settings

PROTOCOL_VERSION = "0.3.0"
MAX_QUESTION = 4000  # 村子的迴圈會把上一版與審查意見一起帶進來，所以比一般問題長


def agent_card(settings: Settings) -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "網路研究員",
        "description": (
            "搜尋網路、閱讀網頁後回答問題，每個說法附上出處（網址、網站、日期）。"
            "適合需要最新或即時資訊的問題：新聞、政策與規定、價格、活動、產品、人物近況。只根據找到的資料回答。"
        ),
        "url": settings.public_url.rstrip("/") + "/a2a",
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "security": [{"bearer": []}],
        "skills": [
            {
                "id": "web_research",
                "name": "網路研究",
                "description": "上網查資料並附引用來源回答，可以回答最新消息、政策、價格等即時資訊",
                "tags": ["搜尋", "新聞", "即時資訊", "研究"],
                "examples": ["2026 年台灣電價有調整嗎？", "最近有什麼重要的科技新聞？", "台北到高雄的高鐵票價是多少？"],
            }
        ],
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


def question_from(params: dict) -> str:
    msg = params.get("message") if isinstance(params, dict) else None
    if not isinstance(msg, dict):
        raise RpcError(-32602, "params.message 是必填的")
    texts = [p.get("text", "") for p in msg.get("parts") or [] if isinstance(p, dict) and p.get("kind") == "text"]
    question = "\n".join(t for t in texts if t).strip()
    if not question:
        raise RpcError(-32602, "訊息裡沒有文字（parts 需要 kind=text）")
    if len(question) > MAX_QUESTION:
        raise RpcError(-32602, f"問題太長（上限 {MAX_QUESTION} 字）")
    return question


def answer_data(result: dict) -> dict:
    return {k: result[k] for k in ("citations", "queries", "failed", "notes", "model", "llm_error", "timings")}


def stream_events(task_id: str, context_id: str):
    def task() -> dict:
        return {"kind": "task", "id": task_id, "contextId": context_id, "status": {"state": "working"}}

    def status(text: str) -> dict:
        return {"kind": "status-update", "taskId": task_id, "contextId": context_id, "final": False,
                "status": {"state": "working", "message": {
                    "kind": "message", "role": "agent", "messageId": uuid.uuid4().hex,
                    "parts": [{"kind": "text", "text": text}]}}}

    def text(piece: str, append: bool) -> dict:
        return {"kind": "artifact-update", "taskId": task_id, "contextId": context_id, "append": append, "lastChunk": False,
                "artifact": {"artifactId": "answer", "parts": [{"kind": "text", "text": piece}]}}

    def data(d: dict) -> dict:
        return {"kind": "artifact-update", "taskId": task_id, "contextId": context_id, "append": False, "lastChunk": True,
                "artifact": {"artifactId": "answer-data", "parts": [{"kind": "data", "data": d}]}}

    def done(state: str = "completed") -> dict:
        return {"kind": "status-update", "taskId": task_id, "contextId": context_id, "status": {"state": state}, "final": True}

    return task, status, text, data, done


def reply(result: dict, context_id: str | None) -> dict:
    return {
        "kind": "message", "role": "agent", "messageId": uuid.uuid4().hex, "contextId": context_id or uuid.uuid4().hex,
        "parts": [{"kind": "text", "text": result["answer"]}, {"kind": "data", "data": answer_data(result)}],
    }
