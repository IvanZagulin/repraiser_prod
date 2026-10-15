"""022 add automatic WB discount pause registry

Revision ID: 20261015_0001_022
Revises: 20260714_0001_021
Create Date: 2026-10-15 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20261015_0001_022"
down_revision = "20260714_0001_021"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "promo_pause_registry" not in inspector.get_table_names():
        op.create_table(
            "promo_pause_registry",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("product_config_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("nm_id", sa.BigInteger(), nullable=False),
            sa.Column("seller_sku", sa.String(length=255), nullable=True),
            sa.Column("discount_pct", sa.Numeric(5, 2), nullable=False),
            sa.Column("paused_at", sa.DateTime(), nullable=False),
            sa.Column("last_checked_at", sa.DateTime(), nullable=False),
            sa.Column("zero_streak", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_paused", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("resumed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["product_config_id"], ["product_config.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("product_config_id"),
        )
        op.create_index(
            "ix_promo_pause_registry_account_paused",
            "promo_pause_registry",
            ["account_id", "is_paused"],
            unique=False,
        )
        op.create_index("ix_promo_pause_registry_nm_id", "promo_pause_registry", ["nm_id"], unique=False)
        op.create_index("ix_promo_pause_registry_product_id", "promo_pause_registry", ["product_id"], unique=False)


def downgrade():
    op.drop_table("promo_pause_registry")

