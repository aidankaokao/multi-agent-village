"""語音轉文字：llm_models.audio_verified_at（轉寫測驗通過的時間；output="audio" 的模型才會有）

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-18 13:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('llm_models', schema=None) as batch_op:
        batch_op.add_column(sa.Column('audio_verified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('llm_models', schema=None) as batch_op:
        batch_op.drop_column('audio_verified_at')
