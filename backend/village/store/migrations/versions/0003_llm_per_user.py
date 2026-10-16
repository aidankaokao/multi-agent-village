"""LLM 設定改為每個帳號各自一份：llm_providers / model_tiers 加上 user_id。

既有的 LLM 設定（還沒有擁有者）全部刪除，各帳號登入後自己重新註冊。
因為資料都要刪掉，直接重建三張表（SQLite 也不用處理未命名的 unique 約束）。

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _providers(with_user: bool) -> None:
    cols = [
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("base_url", sa.String(500)),
        sa.Column("api_key_ct", sa.LargeBinary()),
        sa.Column("api_key_nonce", sa.LargeBinary()),
        sa.Column("key_version", sa.Integer()),
        sa.Column("api_key_hint", sa.String(20)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]
    if with_user:
        cols.insert(1, sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_llm_providers_user"), nullable=False))
        op.create_table("llm_providers", *cols, sa.UniqueConstraint("user_id", "name", name="uq_llm_providers_user_name"))
        op.create_index("ix_llm_providers_user_id", "llm_providers", ["user_id"])
    else:
        op.create_table("llm_providers", *cols, sa.UniqueConstraint("name"))


def _models() -> None:
    op.create_table(
        "llm_models",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey("llm_providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200)),
        sa.Column("supports_json_schema", sa.Boolean(), nullable=False),
        sa.Column("context_window", sa.Integer()),
        sa.Column("reasoning_effort", sa.String(16)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_id", "model"),
    )


def _tiers(with_user: bool) -> None:
    cols = [
        sa.Column("tier", sa.String(16), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("model_id", sa.String(32), sa.ForeignKey("llm_models.id", ondelete="CASCADE"), nullable=False),
    ]
    if with_user:
        cols.insert(0, sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_model_tiers_user"), primary_key=True))
    op.create_table("model_tiers", *cols)


def _drop_all() -> None:
    op.drop_table("model_tiers")
    op.drop_table("llm_models")
    op.drop_table("llm_providers")


def upgrade() -> None:
    _drop_all()
    _providers(with_user=True)
    _models()
    _tiers(with_user=True)


def downgrade() -> None:
    _drop_all()
    _providers(with_user=False)
    _models()
    _tiers(with_user=False)
