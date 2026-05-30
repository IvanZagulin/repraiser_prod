"""add wb_blocked_until to product_config (Price Quarantine)

Revision ID: 20260530_0001_019
Revises: 20260529_0001_018
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa

revision = "20260530_0001_019"
down_revision = "20260529_0001_018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "product_config",
        sa.Column(
            "wb_blocked_until",
            sa.DateTime(),
            nullable=True,
            comment="WB Price Quarantine — репрайсер не трогает цену до этого времени",
        ),
    )


def downgrade():
    op.drop_column("product_config", "wb_blocked_until")
