from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

ProviderKind = Literal["openai_compat", "ollama", "anthropic"]

DEFAULT_BASE_URLS: dict[str, str | None] = {
    "ollama": "http://localhost:11434/v1",
    "openai_compat": None,  # 必填，例如 https://api.openai.com/v1
    "anthropic": None,  # 留空 = 官方端點
}


class LLMError(Exception):
    pass


@dataclass
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


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
    ) -> ChatResult:
        """json_schema 有值時要求模型輸出符合 schema 的 JSON（text 為 JSON 字串）。"""

    @abstractmethod
    async def list_models(self) -> list[RemoteModel]: ...

    async def aclose(self) -> None:
        pass
