"""駐村專家：remote_agents（存取 token 加密、agent card、健康狀態）

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17 21:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('remote_agents',
    sa.Column('agent_id', sa.String(length=64), nullable=False),
    sa.Column('token_ct', sa.LargeBinary(), nullable=True),
    sa.Column('token_nonce', sa.LargeBinary(), nullable=True),
    sa.Column('key_version', sa.Integer(), nullable=True),
    sa.Column('token_hint', sa.String(length=32), nullable=True),
    sa.Column('card', sa.JSON(), nullable=True),
    sa.Column('card_fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('agent_id')
    )


def downgrade() -> None:
    op.drop_table('remote_agents')
