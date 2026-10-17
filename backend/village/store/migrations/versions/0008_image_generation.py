"""生圖：llm_models.output / image_verified_at；chat_images.kind / expires_at，conversation_id 可為空

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17 14:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('llm_models', schema=None) as batch_op:
        batch_op.add_column(sa.Column('output', sa.String(length=8), server_default='text', nullable=False))
        batch_op.add_column(sa.Column('image_verified_at', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('chat_images', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=16), server_default='upload', nullable=False))
        batch_op.add_column(sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.alter_column('conversation_id', existing_type=sa.String(length=32), nullable=True)
        batch_op.create_index(batch_op.f('ix_chat_images_expires_at'), ['expires_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('chat_images', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chat_images_expires_at'))
        batch_op.alter_column('conversation_id', existing_type=sa.String(length=32), nullable=False)
        batch_op.drop_column('expires_at')
        batch_op.drop_column('kind')

    with op.batch_alter_table('llm_models', schema=None) as batch_op:
        batch_op.drop_column('image_verified_at')
        batch_op.drop_column('output')
