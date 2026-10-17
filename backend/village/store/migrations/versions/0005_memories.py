"""長期記憶：memories（書記自動記錄或使用者手動新增）

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17 09:51:37.124918
"""

from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('memories',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('content', sa.String(length=300), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('source_run_id', sa.String(length=32), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('use_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memories_user_id'), ['user_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memories_user_id'))

    op.drop_table('memories')
