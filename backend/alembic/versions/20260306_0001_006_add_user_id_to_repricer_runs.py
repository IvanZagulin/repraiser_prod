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
    # Bootstrap users table if it doesn't exist yet (fresh install path).
    # On existing DBs this is a no-op thanks to IF NOT EXISTS.
    # Full users schema is created by migration 007.
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            email         VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            is_active     BOOLEAN NOT NULL DEFAULT TRUE,
            is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
            created_at    TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

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
