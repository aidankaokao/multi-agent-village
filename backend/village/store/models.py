import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from village.store.db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class LlmProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(32))  # openai_compat | ollama | anthropic
    base_url: Mapped[str | None] = mapped_column(String(500))
    # API key 只以密文存放；明文永遠不落地
    api_key_ct: Mapped[bytes | None] = mapped_column(LargeBinary)
    api_key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(Integer)
    api_key_hint: Mapped[str | None] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    models: Mapped[list["LlmModel"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def api_key_aad(self) -> str:
        return f"llm_provider:{self.id}:api_key"


class LlmModel(Base):
    __tablename__ = "llm_models"
    __table_args__ = (UniqueConstraint("provider_id", "model"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(ForeignKey("llm_providers.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(200))
    supports_json_schema: Mapped[bool] = mapped_column(Boolean, default=True)
    context_window: Mapped[int | None] = mapped_column(Integer)
    # 思考型模型的思考強度：None=provider 預設；none/low/medium/high。本地小模型設 none 可大幅加速
    reasoning_effort: Mapped[str | None] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    provider: Mapped[LlmProvider] = relationship(back_populates="models")


class ModelTier(Base):
    """模型分級：tier（large / small）內依 position 排序，前面失敗就退到下一個。"""

    __tablename__ = "model_tiers"

    tier: Mapped[str] = mapped_column(String(16), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id", ondelete="CASCADE"))


class AgentDef(Base):
    """村民定義。spec 與 agent.yaml 同格式（card / x-village / model / prompt / validate / tests）。"""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    spec: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)  # 從 agents/ 匯入的內建範例
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # 內建範例刪除後保留紀錄，避免重啟又被匯入
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Setting(Base):
    """村規等全域設定：key → JSON（例如 village → 審核員、逾時、平行執行）。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RunTrace(Base):
    """一次問答的執行紀錄：計畫、每一步的輸入輸出、審核結果、耗時與 token。steps 存 JSON（總是跟著 run 一起讀）。"""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    message: Mapped[str] = mapped_column(String(4000))
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)  # running/done/error/cancelled/interrupted
    answer: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(String(2000))
    plan: Mapped[dict | None] = mapped_column(JSON)  # {steps, source, notes}
    steps: Mapped[list | None] = mapped_column(JSON)
    reviews: Mapped[list | None] = mapped_column(JSON)  # 審核員每一輪的判斷
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
