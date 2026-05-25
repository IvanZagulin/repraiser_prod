"""add skip_margin_check to product_config

Revision ID: 20260520_0004_017
Revises: 20260520_0003_016
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "20260520_0004_017"
down_revision = "20260520_0003_016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "product_config",
        sa.Column(
            "skip_margin_check",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Отключить защиту маржи для этого товара",
        ),
    )


def downgrade():
    op.drop_column("product_config", "skip_margin_check")
