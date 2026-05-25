"""add night price boost to product_config

Revision ID: 20260520_0003_016
Revises: 20260520_0002_015
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "20260520_0003_016"
down_revision = "20260520_0002_015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("product_config",
        sa.Column("night_price_enabled", sa.Boolean(), nullable=False,
                  server_default="false",
                  comment="Включить ночное повышение цены"))
    op.add_column("product_config",
        sa.Column("night_start_msk", sa.String(5), nullable=True,
                  comment="Начало ночного окна (МСК), формат HH:MM, напр. '01:00'"))
    op.add_column("product_config",
        sa.Column("night_end_msk", sa.String(5), nullable=True,
                  comment="Конец ночного окна (МСК), формат HH:MM, напр. '07:00'"))
    op.add_column("product_config",
        sa.Column("night_seller_price", sa.Numeric(10, 2), nullable=True,
                  comment="Цена продавца в ночное окно (завышенная для поднятия средней)"))


def downgrade():
    op.drop_column("product_config", "night_seller_price")
    op.drop_column("product_config", "night_end_msk")
    op.drop_column("product_config", "night_start_msk")
    op.drop_column("product_config", "night_price_enabled")
