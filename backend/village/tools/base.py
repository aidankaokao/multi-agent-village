"""工具箱的共用介面。

工具 = 一段可靠的程式碼（計算、畫圖、日期），由「工具型村民」使用：
  LLM 只負責把使用者的話整理成工具要的參數（JSON schema 約束），真正的計算交給程式，弱模型也能得到正確結果。
  LLM 不能用或參數一直錯時，工具可以提供 fallback()：直接從文字裡抓參數。
使用者不需要寫程式：在介面上把村民設成「工具型」並挑一個工具即可。
"""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# 執行中的進度（例如「轉寫第 2 / 6 段」）：run_tool_agent 設定，花很久的工具在 arun 裡呼叫
PROGRESS: ContextVar[Callable[[str], Awaitable[None]] | None] = ContextVar("tool_progress", default=None)


async def report(text: str) -> None:
    if (cb := PROGRESS.get()) is not None:
        await cb(text)


class ToolError(ValueError):
    """參數不合法或算不出來；訊息會回饋給 LLM 讓它修正參數，所以要寫清楚哪裡錯。"""


@dataclass
class Attachment:
    kind: str  # "svg"（data 是 SVG 原始碼）或 "image"（存在 DB 的畫作，用 url 讀取）
    name: str
    data: str = ""
    title: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "name": self.name, "data": self.data, "title": self.title}
        if self.url:
            d["url"] = self.url
        return d


@dataclass
class ToolResult:
    text: str  # 給使用者看、也會交給下一位村民當參考資料
    attachments: list[Attachment] = field(default_factory=list)
    detail: str = ""  # 給 explain 用的精簡事實，例如「35*12*0.9 = 378」


class Tool:
    id: str
    name: str
    description: str  # 給使用者與工匠看：這個工具能做什麼
    default_prompt: str  # 預設的「怎麼從請求取出參數」
    keywords: list[str] = []  # 新建村民時的預設關鍵字
    examples: list[str] = []
    costly = False  # 每次執行都要花錢（例如生圖）：審核員退件時不重做，避免重複計費
    planner_hint = ""  # 附在村長名冊裡的能力說明（不受管理員改村民說明影響）
    timeout_s = 0  # 要跑很久的工具（例如轉寫一小時的錄音）：步驟逾時至少給這麼久

    def schema(self) -> dict:
        """LLM 輸出參數的 JSON schema（strict：所有欄位必填、不允許多餘欄位）。"""
        raise NotImplementedError

    def run(self, args: dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    async def arun(self, args: dict[str, Any]) -> ToolResult:
        """需要呼叫外部服務的工具（例如生圖）覆寫這個；一般工具直接用 run。"""
        return self.run(args)

    def context(self) -> str:
        """每次呼叫時附在參數整理說明後面的即時資訊（例如今天的日期）；自訂說明也會附上。"""
        return ""

    def fallback(self, instruction: str, context: str) -> dict[str, Any] | None:
        """不靠 LLM，直接從文字抓參數；抓不到回 None。"""
        return None

    def info(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "default_prompt": self.default_prompt, "keywords": self.keywords, "examples": self.examples,
        }
