"""Agent 定義（agents/<id>/agent.yaml）的資料模型。

card 段落參考 A2A AgentCard；x-village 是村落專屬欄位（外觀、位置、關鍵字備援）。
"""

from pathlib import Path
from typing import Literal

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


class PromptConfig(BaseModel):
    system: str
    user_template: str = "任務：{instruction}\n{context}"


class Checks(BaseModel):
    must_not_be_empty: bool = True
    max_chars: int | None = None


class AgentSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    card: CardInfo
    village: VillageInfo = Field(default_factory=VillageInfo, alias="x-village")
    kind: Literal["prompt"] = "prompt"  # tool / remote 於 Phase 4 / 6 加入
    model: ModelConfig = Field(default_factory=ModelConfig)
    prompt: PromptConfig | None = None
    checks: Checks = Field(default_factory=Checks, alias="validate")

    @model_validator(mode="after")
    def _prompt_required(self):
        if self.kind == "prompt" and self.prompt is None:
            raise ValueError("kind: prompt 需要 prompt.system")
        return self

    @property
    def id(self) -> str:
        return self.card.id

    @property
    def name(self) -> str:
        return self.card.name


def load_spec(path: Path) -> AgentSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec = AgentSpec.model_validate(data)
    if spec.id != path.parent.name:
        raise ValueError(f"card.id「{spec.id}」必須與資料夾名稱「{path.parent.name}」相同")
    return spec
