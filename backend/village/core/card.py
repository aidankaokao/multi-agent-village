"""村民（agent）定義的資料模型。

村民在介面上建立並存 DB；agents/<id>/agent.yaml 只是第一次啟動時匯入的內建範例，格式相同。
card 段落參考 A2A AgentCard；x-village 是村落專屬欄位（外觀、位置、關鍵字備援）。
"""

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Skill(BaseModel):
    id: str
    description: str
    examples: list[str] = []


class CardInfo(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    name: str
    description: str
    version: str = "0.1.0"
    skills: list[Skill] = []
    tags: list[str] = []


class VillageInfo(BaseModel):
    sprite: str | None = None
    home: tuple[int, int] | None = None
    keywords: list[str] = []


class ModelConfig(BaseModel):
    tier: Literal["large", "small"] = "small"
    model_id: str | None = None  # 指定已註冊模型 id 時，優先於 tier
    temperature: float | None = None
    max_tokens: int = 1024

    @property
    def target(self) -> str:
        return self.model_id or f"tier:{self.tier}"

    @property
    def fallback(self) -> str:
        """指定的模型屬於設定這位村民的人；別人問答時找不到那個模型，就改用分級。"""
        return f"tier:{self.tier}"


class PromptConfig(BaseModel):
    system: str = ""  # prompt 型必填（見 AgentSpec 驗證）；工具型留空 = 用工具的預設說明
    user_template: str = "任務：{instruction}\n{context}"

    @model_validator(mode="after")
    def _has_instruction(self):
        if "{instruction}" not in self.user_template:
            raise ValueError("user_template 必須包含 {instruction}，否則村民收不到任務")
        return self


class ToolConfig(BaseModel):
    """工具型村民：LLM 把請求整理成參數（prompt.system 沒填就用工具的預設說明），工具負責計算。"""

    id: str
    explain: bool = False  # 算完後請 LLM 用一句話說明結果（結果數字仍以工具為準）

    @model_validator(mode="after")
    def _known(self):
        from village.tools import TOOLS  # 延後 import，避免循環

        if self.id not in TOOLS:
            raise ValueError(f"沒有「{self.id}」這個工具，可用：{', '.join(TOOLS)}")
        return self


class RemoteConfig(BaseModel):
    """駐村專家（外接的獨立 agent）：用 A2A 呼叫。存取 token 加密存在 remote_agents 表，不放在這裡。"""

    url: str = Field(pattern=r"^https?://")  # 管理員填的網址，例如 http://host.docker.internal:8100
    a2a_path: str = "/a2a"  # 從對方的 agent card 讀來
    share_llm: bool = False  # 允許把提問者的 LLM 設定（含 key）交給它；預設關閉，管理員確認信任後才打開
    accepts: list[str] = Field(default_factory=list)  # 收哪些文件（副檔名，從名片讀來，例如 [".xlsx", ".pdf"]）
    max_files: int = 3  # 一次最多收幾份（名片的 x-file-limits.maxFiles；例如報社收 6 份）


class Checks(BaseModel):
    must_not_be_empty: bool = True
    max_chars: int | None = None


class Expect(BaseModel):
    """測試題的期望條件，全部都要符合才算通過；沒填的不檢查。"""

    contains: list[str] = []
    not_contains: list[str] = []
    lang: Literal["zh", "en"] | None = None  # zh：要有中文字；en：不能有中文字
    min_chars: int | None = None
    max_chars: int | None = None
    regex: str | None = None

    @model_validator(mode="after")
    def _regex_ok(self):
        if self.regex:
            try:
                re.compile(self.regex)
            except re.error as e:
                raise ValueError(f"regex 格式錯誤：{e}") from None
        return self


class AgentTest(BaseModel):
    input: str = Field(min_length=1)  # 村長會給的指示
    context: str = ""  # 模擬前一步的成果（選填）
    expect: Expect = Field(default_factory=Expect)


class AgentSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    card: CardInfo
    village: VillageInfo = Field(default_factory=VillageInfo, alias="x-village")
    kind: Literal["prompt", "tool", "remote"] = "prompt"  # remote = 駐村專家（外接的獨立 agent）
    model: ModelConfig = Field(default_factory=ModelConfig)
    prompt: PromptConfig | None = None
    tool: ToolConfig | None = None
    remote: RemoteConfig | None = None
    checks: Checks = Field(default_factory=Checks, alias="validate")
    tests: list[AgentTest] = []

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.kind == "prompt" and (self.prompt is None or not self.prompt.system.strip()):
            raise ValueError("kind: prompt 需要 prompt.system")
        if self.kind == "tool" and self.tool is None:
            raise ValueError("kind: tool 需要選一個工具（tool.id）")
        if self.kind == "remote" and self.remote is None:
            raise ValueError("kind: remote 需要 remote.url（駐村專家的網址）")
        if self.prompt is None:  # 工具型可以不寫 prompt（用工具的預設說明）；補上空的，介面才有欄位可以填
            self.prompt = PromptConfig()
        return self

    @property
    def id(self) -> str:
        return self.card.id

    @property
    def name(self) -> str:
        return self.card.name

    def to_doc(self) -> dict[str, Any]:
        """存 DB / 匯出 YAML 用的格式（與 agent.yaml 相同的欄位名稱）。"""
        return self.model_dump(mode="json", by_alias=True)


def load_spec(path: Path) -> AgentSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec = AgentSpec.model_validate(data)
    if spec.id != path.parent.name:
        raise ValueError(f"card.id「{spec.id}」必須與資料夾名稱「{path.parent.name}」相同")
    return spec
