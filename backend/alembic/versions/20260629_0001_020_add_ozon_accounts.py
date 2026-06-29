"""020 add ozon account credentials

Revision ID: 020_add_ozon_accounts
Revises: 20260530_0001_019
Create Date: 2026-06-29 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "020_add_ozon_accounts"
down_revision = "20260530_0001_019"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "accounts",
        sa.Column("marketplace", sa.String(length=20), nullable=False, server_default="wb"),
    )
    op.add_column("accounts", sa.Column("ozon_client_id", sa.Text(), nullable=True))
    op.add_column("accounts", sa.Column("ozon_api_key", sa.Text(), nullable=True))
    op.alter_column("accounts", "wb_api_token", existing_type=sa.Text(), nullable=True)
    op.create_index(op.f("ix_accounts_marketplace"), "accounts", ["marketplace"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_accounts_marketplace"), table_name="accounts")
    op.alter_column("accounts", "wb_api_token", existing_type=sa.Text(), nullable=False)
    op.drop_column("accounts", "ozon_api_key")
    op.drop_column("accounts", "ozon_client_id")
    op.drop_column("accounts", "marketplace")
