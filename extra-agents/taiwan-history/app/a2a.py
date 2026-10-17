"""A2A（Agent2Agent）介面：讓其他系統（例如 Multi-Agent Village）用標準格式呼叫這個 agent。

- GET  /.well-known/agent-card.json   自我介紹（名稱、技能、怎麼呼叫、需要哪些 header）；公開
- POST /a2a                            JSON-RPC 2.0，method = "message/send"；需要存取 token

回答用的 LLM 一樣放在 HTTP header（X-LLM-Base-URL、X-LLM-Model、X-LLM-API-Key、X-LLM-Reasoning-Effort），
不放在訊息內容裡（訊息可能被呼叫端存成紀錄，key 不該跟著被存）。
回覆的 Message 有兩個 part：text（回答）與 data（引用來源、使用的模型、耗時），呼叫端可以只取 text。
"""

import uuid

from app.config import Settings

PROTOCOL_VERSION = "0.3.0"


def agent_card(settings: Settings) -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "台灣史專家",
        "description": (
            "以中文維基百科的台灣史條目（史前、原住民族、荷西、明鄭、清治、日治、戰後、民主化與當代，共 175 篇）為資料，"
            "回答台灣歷史問題並附上引用來源。只根據資料回答，資料沒有的會說明。"
        ),
        "url": settings.public_url.rstrip("/") + "/a2a",
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "security": [{"bearer": []}],
        "skills": [
            {
                "id": "taiwan_history_qa",
                "name": "台灣史問答",
                "description": "回答台灣歷史的事件、人物、制度、年代等問題，附引用來源（條目、章節、網址）",
                "tags": ["台灣史", "歷史", "RAG"],
                "examples": ["霧社事件的起因是什麼？", "嘉南大圳是誰設計的？", "臺灣第一位民選總統是誰？"],
            }
        ],
        # 這個 agent 的擴充說明：回答用的 LLM 由呼叫端提供
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
    if len(question) > 1000:
        raise RpcError(-32602, "問題太長（上限 1000 字）")
    return question


def reply(answer: dict, context_id: str | None) -> dict:
    return {
        "kind": "message",
        "role": "agent",
        "messageId": uuid.uuid4().hex,
        "contextId": context_id or uuid.uuid4().hex,
        "parts": [
            {"kind": "text", "text": answer["answer"]},
            {"kind": "data", "data": {k: answer[k] for k in ("citations", "model", "llm_error", "timings")}},
        ],
    }
