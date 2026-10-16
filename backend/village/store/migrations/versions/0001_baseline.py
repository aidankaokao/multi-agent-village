"""baseline（Phase 4 的資料表）

Revision ID: 0001
Revises: 
Create Date: 2026-10-17 05:19:19.489220
"""

from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('agents',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('spec', sa.JSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('builtin', sa.Boolean(), nullable=False),
    sa.Column('deleted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('llm_providers',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('base_url', sa.String(length=500), nullable=True),
    sa.Column('api_key_ct', sa.LargeBinary(), nullable=True),
    sa.Column('api_key_nonce', sa.LargeBinary(), nullable=True),
    sa.Column('key_version', sa.Integer(), nullable=True),
    sa.Column('api_key_hint', sa.String(length=20), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('runs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('message', sa.String(length=4000), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('answer', sa.String(), nullable=True),
    sa.Column('error', sa.String(length=2000), nullable=True),
    sa.Column('plan', sa.JSON(), nullable=True),
    sa.Column('steps', sa.JSON(), nullable=True),
    sa.Column('reviews', sa.JSON(), nullable=True),
    sa.Column('elapsed_ms', sa.Integer(), nullable=True),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_runs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_runs_status'), ['status'], unique=False)

    op.create_table('settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('llm_models',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('provider_id', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=True),
    sa.Column('supports_json_schema', sa.Boolean(), nullable=False),
    sa.Column('context_window', sa.Integer(), nullable=True),
    sa.Column('reasoning_effort', sa.String(length=16), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['provider_id'], ['llm_providers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider_id', 'model')
    )
    op.create_table('model_tiers',
    sa.Column('tier', sa.String(length=16), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('model_id', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['model_id'], ['llm_models.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('tier', 'position')
    )


def downgrade() -> None:
    op.drop_table('model_tiers')
    op.drop_table('llm_models')
    op.drop_table('settings')
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_runs_status'))
        batch_op.drop_index(batch_op.f('ix_runs_created_at'))

    op.drop_table('runs')
    op.drop_table('llm_providers')
    op.drop_table('agents')
