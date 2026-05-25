"""add successful price change lookup index

Revision ID: 20260517_0001_013
Revises: 20260507_0001_012
Create Date: 2026-05-17
"""
from alembic import op

revision = "20260517_0001_013"
down_revision = "20260507_0001_012"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("COMMIT")
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_price_changes_success_product_changed_id
        ON price_changes (product_id, changed_at DESC, id DESC)
        INCLUDE (new_seller_price, new_final_price)
        WHERE new_seller_price IS NOT NULL
          AND reason_code IN ('UPDATED', 'CLAMPED_TO_MIN', 'CLAMPED_TO_MAX', 'CLAMPED_TO_MARGIN')
        """
    )


def downgrade():
    op.execute("COMMIT")
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS ix_price_changes_success_product_changed_id"
    )
