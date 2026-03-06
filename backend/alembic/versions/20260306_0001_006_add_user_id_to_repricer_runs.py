"""006 add user_id to repricer_runs

Revision ID: 006_repricer_runs_user_id
Revises: 005_nullable_sku_name
Create Date: 2026-03-06
"""
from alembic import op
import sqlalchemy as sa

revision = '006_repricer_runs_user_id'
down_revision = '005_nullable_sku_name'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'repricer_runs',
        sa.Column('user_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_repricer_runs_user_id',
        'repricer_runs', 'users',
        ['user_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_repricer_runs_user_id', 'repricer_runs', ['user_id'])


def downgrade():
    op.drop_index('ix_repricer_runs_user_id', 'repricer_runs')
    op.drop_constraint('fk_repricer_runs_user_id', 'repricer_runs', type_='foreignkey')
    op.drop_column('repricer_runs', 'user_id')
