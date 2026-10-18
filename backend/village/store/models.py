import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from village.store.db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class LlmProvider(Base):
    """LLM 供應商。每個帳號各自註冊、各自的 API key 與額度（帳號刪除時一併刪除）。"""

    __tablename__ = "llm_providers"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_llm_providers_user_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
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
        # 密文綁定擁有者與這筆 provider：搬到別人名下或別的 provider 都解不開
        return f"llm_provider:{self.user_id}:{self.id}:api_key"


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
    # 看圖測驗通過的時間；None = 沒測過或沒通過。只有通過的模型能放進 vision 分級（鑑定師專用）
    vision_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 輸出什麼：text（對話 / 看圖）、image（生圖）或 audio（語音轉文字）。後兩種只能放各自的分級，不能拿來對話
    output: Mapped[str] = mapped_column(String(8), default="text", server_default="text")
    image_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 生圖測驗通過的時間
    audio_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 轉寫測驗通過的時間
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    provider: Mapped[LlmProvider] = relationship(back_populates="models")


class ModelTier(Base):
    """模型分級（每個帳號各自一份）：tier（large / small）內依 position 排序，前面失敗就退到下一個。"""

    __tablename__ = "model_tiers"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
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


class RemoteAgent(Base):
    """駐村專家的連線資訊（村民定義在 agents 表，kind=remote）：存取 token（加密）、讀到的 agent card、健康狀態。"""

    __tablename__ = "remote_agents"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    token_ct: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(Integer)
    token_hint: Mapped[str | None] = mapped_column(String(32))
    card: Mapped[dict | None] = mapped_column(JSON)  # 對方的 agent card（原樣保存）
    card_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # online / offline / unknown
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))

    @property
    def token_aad(self) -> str:
        return f"remote_agent:{self.agent_id}:token"


class Setting(Base):
    """村規等全域設定：key → JSON（例如 village → 審核員、逾時、平行執行）。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class User(Base):
    """帳號。第一個建立的帳號是管理員；之後註冊或由管理員新增的是一般成員。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True)  # 一律小寫
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(256))  # scrypt，格式見 security/auth.py
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin / member
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """登入 session：cookie 裡放隨機 token，DB 只存它的 SHA-256（DB 外洩也拿不到可用的 token）。"""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class Conversation(Base):
    """一段對話（屬於某個帳號）。訊息另外存在 messages，不受執行紀錄的保留筆數影響。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Message(Base):
    """對話中的一則訊息。assistant 訊息附上 run_id（執行紀錄可能已被清掉，所以不設外鍵）。"""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="done")  # done / error / cancelled
    meta: Mapped[dict | None] = mapped_column(JSON)  # {attachments, review, agents}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChatImage(Base):
    """問答的圖片，只有本人看得到；刪對話或帳號時一起刪。

    kind=upload：使用者附的圖（前端已縮圖、去掉 EXIF），跟著對話保存。
    kind=generated：畫家（生圖模型）畫的圖，expires_at 到了自動清掉（很少回頭查，不用存太久）；
    在村民試打或生圖測驗產生的沒有對話（conversation_id 為空）。
    """

    __tablename__ = "chat_images"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="upload", server_default="upload")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    mime: Mapped[str] = mapped_column(String(32))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChatFile(Base):
    """問答的文件，只有本人拿得到；刪對話或帳號時一起刪。

    kind=upload：使用者附的文件（xlsx / csv / txt / pdf），跟著對話保存；追問時再交給駐村專家（對方不存檔）。
    kind=report：駐村專家回傳的檔案（例如文件彙整師的報告 PDF）或聽打員的逐字稿，expires_at 到了自動清掉（同畫作）。
    kind=audio：上傳的錄音（先上傳、問答時才接到對話；沒接上的 1 天後清掉，轉完逐字稿就刪掉）。
    """

    __tablename__ = "chat_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="upload")
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Memory(Base):
    """長期記憶：關於使用者本人、跨對話有效的事（名字、偏好、事實）。由書記自動記錄或使用者手動新增。"""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16), default="fact")  # preference / fact
    source: Mapped[str] = mapped_column(String(16), default="auto")  # auto（書記）/ manual
    source_run_id: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunTrace(Base):
    """一次問答的執行紀錄：計畫、每一步的輸入輸出、審核結果、耗時與 token。steps 存 JSON（總是跟著 run 一起讀）。"""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
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
