"""007 catch-up: move all startup_db_migration DDL into Alembic

All statements use IF NOT EXISTS / conditional DO-blocks so this migration
is safe to run on an existing production DB that already has these objects.

For fresh installs startup_db_migration bootstraps a minimal users table
before running `alembic upgrade head`, so the 006 FK to users never fails.

Revision ID: 007_catch_up_full_schema
Revises: 006_repricer_runs_user_id
Create Date: 2026-03-22
"""
from alembic import op

revision = '007_catch_up_full_schema'
down_revision = '006_repricer_runs_user_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. users — base table + all columns
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                  SERIAL PRIMARY KEY,
            email               VARCHAR(255) UNIQUE NOT NULL,
            password_hash       VARCHAR(255) NOT NULL,
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            is_admin            BOOLEAN NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON users(email)")

    for col_ddl in [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified          BOOLEAN   NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_code    VARCHAR(6)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_code_expires_at TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_code           VARCHAR(6)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_code_expires_at TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_attempts SMALLINT NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_attempts       SMALLINT NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version        SMALLINT NOT NULL DEFAULT 1",
    ]:
        op.execute(col_ddl)

    # ------------------------------------------------------------------
    # 2. accounts.user_id FK
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS "
        "user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_accounts_user_id ON accounts(user_id)")

    # ------------------------------------------------------------------
    # 3. app_settings.user_id + composite unique constraint
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS "
        "user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_app_settings_user_id ON app_settings(user_id)")

    # Drop old single-column unique constraint/index (any of the possible names)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'app_settings_key_key'
                  AND conrelid = 'app_settings'::regclass
            ) THEN
                ALTER TABLE app_settings DROP CONSTRAINT app_settings_key_key;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ix_app_settings_key'
                  AND conrelid = 'app_settings'::regclass
            ) THEN
                ALTER TABLE app_settings DROP CONSTRAINT ix_app_settings_key;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ix_app_settings_key' AND tablename = 'app_settings'
            ) THEN
                DROP INDEX ix_app_settings_key;
            END IF;
        END$$
    """)

    # Composite unique (key, user_id)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_app_settings_key_user'
            ) THEN
                ALTER TABLE app_settings
                ADD CONSTRAINT uq_app_settings_key_user UNIQUE (key, user_id);
            END IF;
        END$$
    """)

    # ------------------------------------------------------------------
    # 4. products extra columns
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS cost_price NUMERIC(10, 2)")
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS brand      VARCHAR(200)")
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS category   VARCHAR(200)")

    # ------------------------------------------------------------------
    # 5. product_config.competitor_select_rule
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE product_config ADD COLUMN IF NOT EXISTS "
        "competitor_select_rule VARCHAR(20) DEFAULT 'min_price'"
    )

    # ------------------------------------------------------------------
    # 6. competitor_entries
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS competitor_entries (
            id                SERIAL PRIMARY KEY,
            product_config_id INTEGER NOT NULL
                REFERENCES product_config(id) ON DELETE CASCADE,
            nm_id             BIGINT NOT NULL,
            alias             VARCHAR(100)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_competitor_entries_product_config_id "
        "ON competitor_entries(product_config_id)"
    )

    # ------------------------------------------------------------------
    # 7. wb_catalog
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS wb_catalog (
            id          SERIAL PRIMARY KEY,
            account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            nm_id       BIGINT NOT NULL,
            vendor_code VARCHAR(255) NOT NULL,
            synced_at   TIMESTAMP NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_wb_catalog_account_sku UNIQUE (account_id, vendor_code)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wb_catalog_account_id ON wb_catalog(account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wb_catalog_nm_id      ON wb_catalog(account_id, nm_id)")
    op.execute("ALTER TABLE wb_catalog ADD COLUMN IF NOT EXISTS image_url VARCHAR(512)")

    # Backfill products.image_url from wb_catalog where available
    op.execute("""
        UPDATE products p
        SET image_url = c.image_url
        FROM wb_catalog c
        WHERE p.account_id = c.account_id
          AND p.nm_id      = c.nm_id
          AND p.image_url  IS NULL
          AND c.image_url  IS NOT NULL
    """)

    # ------------------------------------------------------------------
    # 8. Performance indexes
    # ------------------------------------------------------------------
    op.execute("CREATE INDEX IF NOT EXISTS ix_products_account_id ON products(account_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_price_snapshots_product_snapshot "
        "ON price_snapshots(product_id, snapshot_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_repricer_runs_status_started "
        "ON repricer_runs(status, started_at DESC)"
    )

    # ------------------------------------------------------------------
    # 9. plans table + seed rows
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id         SERIAL PRIMARY KEY,
            code       VARCHAR(50)  UNIQUE NOT NULL,
            name       VARCHAR(100) NOT NULL,
            max_sku    INTEGER,
            max_accounts INTEGER,
            price_rub  NUMERIC(10, 2),
            is_active  BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    # Seed only when table is empty
    op.execute("""
        INSERT INTO plans (code, name, max_sku, max_accounts, price_rub)
        SELECT * FROM (VALUES
            ('start',      'Старт',      500,  1, 2990),
            ('business',   'Бизнес',     5000, 5, 6990),
            ('enterprise', 'Энтерпрайз', NULL, NULL, NULL)
        ) AS v(code, name, max_sku, max_accounts, price_rub)
        WHERE NOT EXISTS (SELECT 1 FROM plans)
    """)

    # ------------------------------------------------------------------
    # 10. users plan / trial / onboarding columns
    # ------------------------------------------------------------------
    for col_ddl in [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_id            INTEGER REFERENCES plans(id)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_assigned_at   TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_started_at   TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_ends_at      TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE",
    ]:
        op.execute(col_ddl)

    # ------------------------------------------------------------------
    # 11. repricer_schedules
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS repricer_schedules (
            id               SERIAL PRIMARY KEY,
            user_id          INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            interval_minutes INTEGER NOT NULL DEFAULT 60,
            work_start_hour  INTEGER NOT NULL DEFAULT 0,
            work_end_hour    INTEGER NOT NULL DEFAULT 24,
            timezone         VARCHAR(50) NOT NULL DEFAULT 'Europe/Moscow',
            paused_days      VARCHAR(50),
            is_active        BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at       TIMESTAMP DEFAULT NOW()
        )
    """)

    # ------------------------------------------------------------------
    # 12. activity_logs
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event_type VARCHAR(50) NOT NULL,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
            details    TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_activity_logs_user_created "
        "ON activity_logs(user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_activity_logs_event_type "
        "ON activity_logs(event_type)"
    )

    # ------------------------------------------------------------------
    # 13. config_change_log
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS config_change_log (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            field_name VARCHAR(50) NOT NULL,
            old_value  VARCHAR(255),
            new_value  VARCHAR(255),
            changed_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_config_change_log_user "
        "ON config_change_log(user_id, changed_at DESC)"
    )

    # ------------------------------------------------------------------
    # 14. product_groups + membership
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_groups (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name       VARCHAR(100) NOT NULL,
            color      VARCHAR(7) NOT NULL DEFAULT '#7c3aed',
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_groups_user ON product_groups(user_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS product_group_membership (
            id         SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id)       ON DELETE CASCADE,
            group_id   INTEGER NOT NULL REFERENCES product_groups(id) ON DELETE CASCADE,
            CONSTRAINT uq_product_group UNIQUE (product_id, group_id)
        )
    """)

    # ------------------------------------------------------------------
    # 15. competitor_alerts
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS competitor_alerts (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
            product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            competitor_nm_id BIGINT NOT NULL,
            old_price       NUMERIC(10, 2) NOT NULL,
            new_price       NUMERIC(10, 2) NOT NULL,
            change_pct      NUMERIC(5, 1) NOT NULL,
            is_read         BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_competitor_alerts_user "
        "ON competitor_alerts(user_id, is_read, created_at DESC)"
    )

    # ------------------------------------------------------------------
    # 16. pricing_rules
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS pricing_rules (
            id                SERIAL PRIMARY KEY,
            product_config_id INTEGER NOT NULL REFERENCES product_config(id) ON DELETE CASCADE,
            priority          INTEGER NOT NULL DEFAULT 0,
            condition_type    VARCHAR(30) NOT NULL,
            condition_value   VARCHAR(50),
            action_type       VARCHAR(30) NOT NULL,
            action_value      VARCHAR(50) NOT NULL,
            is_active         BOOLEAN NOT NULL DEFAULT TRUE,
            created_at        TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pricing_rules_config "
        "ON pricing_rules(product_config_id)"
    )


def downgrade() -> None:
    # Намеренно пустой: откат всей схемы назад на проде опасен.
    # При необходимости — делать точечные downgrade-миграции вручную.
    pass
