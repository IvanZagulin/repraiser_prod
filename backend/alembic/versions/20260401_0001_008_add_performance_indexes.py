"""add_performance_indexes

Revision ID: 008_add_performance_indexes
Revises: 007_catch_up_full_schema
Create Date: 2026-04-01

"""
from alembic import op

revision = '008_add_performance_indexes'
down_revision = '007_catch_up_full_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Индексы для ускорения поиска пользователей
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_email ON users (email)")

    # Индексы для ускорения поиска аккаунтов по пользователю
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_accounts_user_id ON accounts (user_id)")

    # Индексы для ускорения поиска продуктов по аккаунту
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_products_account_id ON products (account_id)")

    # Индексы для ускорения поиска price snapshots по продукту и времени
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_price_snapshots_product_id ON price_snapshots (product_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_price_snapshots_snapshot_at ON price_snapshots (snapshot_at)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_price_snapshots_product_time ON price_snapshots (product_id, snapshot_at)")

    # Индексы для repricer runs
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_repricer_runs_user_id ON repricer_runs (user_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_repricer_runs_status ON repricer_runs (status)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_repricer_runs_created_at ON repricer_runs (created_at)")

    # Индексы для price changes
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_price_changes_product_id ON price_changes (product_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_price_changes_run_id ON price_changes (repricer_run_id)")

    # Индексы для competitor entries
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_competitor_entries_product_config_id ON competitor_entries (product_config_id)")

    # Индексы для activity logs
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_activity_logs_user_id ON activity_logs (user_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_activity_logs_action ON activity_logs (action)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_activity_logs_created_at ON activity_logs (created_at)")


def downgrade() -> None:
    # Удаляем индексы в обратном порядке
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_activity_logs_created_at")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_activity_logs_action")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_activity_logs_user_id")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_competitor_entries_product_config_id")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_price_changes_run_id")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_price_changes_product_id")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_repricer_runs_created_at")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_repricer_runs_status")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_repricer_runs_user_id")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_price_snapshots_product_time")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_price_snapshots_snapshot_at")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_price_snapshots_product_id")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_products_account_id")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_accounts_user_id")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_users_email")
