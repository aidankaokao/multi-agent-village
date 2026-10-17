import os
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

ProviderKind = Literal["openai_compat", "ollama", "anthropic"]
ReasoningEffort = Literal["none", "low", "medium", "high"]

DEFAULT_BASE_URLS: dict[str, str | None] = {
    # 容器內要連主機上的 Ollama 時設 VILLAGE_OLLAMA_URL=http://host.docker.internal:11434/v1
    "ollama": os.environ.get("VILLAGE_OLLAMA_URL") or "http://localhost:11434/v1",
    "openai_compat": None,  # 必填，例如 https://api.openai.com/v1
    "anthropic": None,  # 留空 = 官方端點
}


TextCallback = Callable[[str], Awaitable[None]]


class LLMError(Exception):
    pass


@dataclass
class ImagePart:
    """附在訊息上的圖片（只有視覺模型看得懂）。data 是 base64（不含 data: 前綴）。"""

    mime: str
    data: str

    @property
    def data_url(self) -> str:
        return f"data:{self.mime};base64,{self.data}"


@dataclass
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str
    images: list[ImagePart] = field(default_factory=list)


@dataclass
class ChatResult:
    text: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class RemoteModel:
    id: str
    display_name: str | None = None
    context_window: int | None = None


class LLMClient(ABC):
    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float | None = None,
        json_schema: dict | None = None,
        reasoning_effort: str | None = None,
        on_delta: TextCallback | None = None,
    ) -> ChatResult:
        """json_schema 有值時要求模型輸出符合 schema 的 JSON（text 為 JSON 字串）。

        on_delta 有值時改用串流，每收到一段文字就呼叫一次（傳入的是增量）；回傳值仍是完整結果。

        輸出被截斷且沒有任何內容時（常見於思考型模型把 token 用在思考上）要 raise LLMError，
        讓上層備援接手，而不是回傳空字串。
        """

    @abstractmethod
    async def list_models(self) -> list[RemoteModel]: ...

    async def aclose(self) -> None:
        pass
