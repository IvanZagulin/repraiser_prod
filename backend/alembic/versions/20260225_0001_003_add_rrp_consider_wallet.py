"""Add rrp_consider_wallet to product_config

Revision ID: 003_rrp_consider_wallet
Revises: 002_app_settings
Create Date: 2026-02-25 00:01:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '003_rrp_consider_wallet'
down_revision = '002_app_settings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'product_config',
        sa.Column(
            'rrp_consider_wallet',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
            comment='Учитывать скидку кошелька WB при расчёте РРЦ'
        )
    )


def downgrade() -> None:
    op.drop_column('product_config', 'rrp_consider_wallet')
