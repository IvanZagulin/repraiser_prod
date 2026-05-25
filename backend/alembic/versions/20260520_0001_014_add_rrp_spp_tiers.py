"""add rrp_spp_tiers table

Revision ID: 20260520_0001_014
Revises: 20260517_0001_013
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "20260520_0001_014"
down_revision = "20260517_0001_013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rrp_spp_tiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_config_id",
            sa.Integer(),
            sa.ForeignKey("product_config.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("spp_min",   sa.Numeric(5, 2), nullable=False),
        sa.Column("spp_max",   sa.Numeric(5, 2), nullable=True),
        sa.Column("rrp_price", sa.Numeric(10, 2), nullable=False),
        sa.CheckConstraint("spp_min >= 0",
                           name="check_rrp_tier_spp_min_non_negative"),
        sa.CheckConstraint("spp_max IS NULL OR spp_max > spp_min",
                           name="check_rrp_tier_spp_max_gt_min"),
        sa.CheckConstraint("rrp_price > 0",
                           name="check_rrp_tier_price_positive"),
    )


def downgrade():
    op.drop_table("rrp_spp_tiers")
