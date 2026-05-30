"""add match_competitor_basic to product_config

Revision ID: 20260529_0001_018
Revises: 20260520_0004_017
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa

revision = "20260529_0001_018"
down_revision = "20260520_0004_017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "product_config",
        sa.Column(
            "match_competitor_basic",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Имитировать зачёркнутую цену конкурента (priceU = competitor.basic, скидка подбирается под target_final)",
        ),
    )


def downgrade():
    op.drop_column("product_config", "match_competitor_basic")
