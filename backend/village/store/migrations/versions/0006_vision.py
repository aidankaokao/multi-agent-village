"""看圖測驗：llm_models.vision_verified_at（通過才能放進 vision 分級）

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17 10:33:43.174076
"""

from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('llm_models', schema=None) as batch_op:
        batch_op.add_column(sa.Column('vision_verified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('llm_models', schema=None) as batch_op:
        batch_op.drop_column('vision_verified_at')
