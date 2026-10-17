"""問答附圖：chat_images（跟著對話保存，只有本人看得到）

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17 11:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('chat_images',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('conversation_id', sa.String(length=32), nullable=False),
    sa.Column('mime', sa.String(length=32), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chat_images', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_images_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_images_conversation_id'), ['conversation_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('chat_images', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chat_images_conversation_id'))
        batch_op.drop_index(batch_op.f('ix_chat_images_user_id'))

    op.drop_table('chat_images')
