"""add_composite_indexes

Revision ID: 009_add_composite_indexes
Revises: 008_add_performance_indexes
Create Date: 2026-04-04

"""
from alembic import op

revision = '009_add_composite_indexes'
down_revision = '008_add_performance_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("COMMIT")

    # Composite index для price_changes: запросы часто фильтруют по product_id + сортируют по changed_at
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_price_changes_product_id_changed_at ON price_changes (product_id, changed_at)")

    # Composite index для activity_logs: запросы фильтруют по user_id + event_type + сортируют по created_at
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_activity_logs_user_id_event_type_created_at ON activity_logs (user_id, event_type, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_activity_logs_user_id_event_type_created_at")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_price_changes_product_id_changed_at")
