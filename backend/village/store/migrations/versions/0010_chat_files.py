"""問答附文件：chat_files（上傳的文件跟著對話保存；駐村專家回傳的報告 14 天過期）

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-18 09:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('chat_files',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('conversation_id', sa.String(length=32), nullable=True),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('mime', sa.String(length=100), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_chat_files_user_id', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='fk_chat_files_conversation_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chat_files', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_files_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_files_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_files_expires_at'), ['expires_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('chat_files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chat_files_expires_at'))
        batch_op.drop_index(batch_op.f('ix_chat_files_conversation_id'))
        batch_op.drop_index(batch_op.f('ix_chat_files_user_id'))

    op.drop_table('chat_files')
