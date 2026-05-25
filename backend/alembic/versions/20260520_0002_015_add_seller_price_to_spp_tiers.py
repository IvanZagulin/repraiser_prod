"""add seller_price to rrp_spp_tiers, make rrp_price nullable

Revision ID: 20260520_0002_015
Revises: 20260520_0001_014
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "20260520_0002_015"
down_revision = "20260520_0001_014"
branch_labels = None
depends_on = None


def upgrade():
    # Add seller_price column (direct WB seller price, before discounts/SPP)
    op.add_column(
        "rrp_spp_tiers",
        sa.Column("seller_price", sa.Numeric(10, 2), nullable=True,
                  comment="Direct seller price sent to WB (before discounts/SPP). "
                          "If set, used as-is instead of calculating from rrp_price."),
    )
    # Make rrp_price nullable (was NOT NULL)
    op.alter_column("rrp_spp_tiers", "rrp_price", nullable=True)
    # At least one of rrp_price or seller_price must be set
    op.create_check_constraint(
        "check_rrp_tier_has_price",
        "rrp_spp_tiers",
        "(rrp_price IS NOT NULL AND rrp_price > 0) OR "
        "(seller_price IS NOT NULL AND seller_price > 0)",
    )


def downgrade():
    op.drop_constraint("check_rrp_tier_has_price", "rrp_spp_tiers", type_="check")
    op.alter_column("rrp_spp_tiers", "rrp_price", nullable=False)
    op.drop_column("rrp_spp_tiers", "seller_price")
