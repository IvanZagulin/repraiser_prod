"""021 add WB subject commissions

Revision ID: 20260714_0001_021
Revises: 020_add_ozon_accounts
Create Date: 2026-07-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_0001_021"
down_revision = "020_add_ozon_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("products", sa.Column("subject_name", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_products_subject_name"), "products", ["subject_name"], unique=False)

    op.add_column("wb_catalog", sa.Column("subject_name", sa.String(length=255), nullable=True))

    op.create_table(
        "wb_subject_commissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_name", sa.String(length=255), nullable=False),
        sa.Column("commission_pct", sa.Numeric(6, 2), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_name"),
    )
    op.create_index(op.f("ix_wb_subject_commissions_id"), "wb_subject_commissions", ["id"], unique=False)
    op.create_index(
        op.f("ix_wb_subject_commissions_subject_name"),
        "wb_subject_commissions",
        ["subject_name"],
        unique=True,
    )
    op.execute(sa.text("""
        INSERT INTO app_settings (key, value, user_id, updated_at)
        SELECT
            :key,
            :value,
            NULL,
            NOW()
        WHERE NOT EXISTS (
            SELECT 1 FROM app_settings
            WHERE key = :key AND user_id IS NULL
        )
    """).bindparams(
        key="brand_min_margin_rules",
        value='[{"brand":"Просвещение","min_margin_pct":20.0}]',
    ))


def downgrade():
    op.execute(sa.text("""
        DELETE FROM app_settings
        WHERE key = :key
          AND user_id IS NULL
          AND value = :value
    """).bindparams(
        key="brand_min_margin_rules",
        value='[{"brand":"Просвещение","min_margin_pct":20.0}]',
    ))

    op.drop_index(op.f("ix_wb_subject_commissions_subject_name"), table_name="wb_subject_commissions")
    op.drop_index(op.f("ix_wb_subject_commissions_id"), table_name="wb_subject_commissions")
    op.drop_table("wb_subject_commissions")

    op.drop_column("wb_catalog", "subject_name")

    op.drop_index(op.f("ix_products_subject_name"), table_name="products")
    op.drop_column("products", "subject_name")
