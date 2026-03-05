"""Add app_settings table

Revision ID: 002_app_settings
Revises: 001_initial
Create Date: 2026-02-24 00:01:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '002_app_settings'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'app_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_app_settings_id'), 'app_settings', ['id'], unique=False)
    op.create_index(op.f('ix_app_settings_key'), 'app_settings', ['key'], unique=True)

    # Вставляем дефолтные настройки
    op.execute(
        "INSERT INTO app_settings (key, value, updated_at) "
        "VALUES ('repricer_interval_minutes', '60', CURRENT_TIMESTAMP)"
    )
    op.execute(
        "INSERT INTO app_settings (key, value, updated_at) "
        "VALUES ('wb_wallet_discount_pct', '3.0', CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_app_settings_key'), table_name='app_settings')
    op.drop_index(op.f('ix_app_settings_id'), table_name='app_settings')
    op.drop_table('app_settings')
