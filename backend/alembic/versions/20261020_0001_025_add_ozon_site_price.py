"""025 add ozon site price to product_config and price_snapshots

Revision ID: 20261020_0001_025
Revises: 20261015_0003_024
Create Date: 2026-10-20 10:00:00.000000

«Цена на сайте» из отчёта Ozon (/v1/report/products/create) — прямое
измерение витрины вместо обратного счёта по ценовому индексу. Храним само
значение, момент замера и цену продавца, при которой он сделан: без последней
нельзя понять, успел ли Ozon пересчитать витрину после нашей правки цены.

Все колонки nullable — для WB-товаров они остаются пустыми.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261020_0001_025"
down_revision = "20261015_0003_024"
branch_labels = None
depends_on = None


CONFIG_COLUMNS = [
    ("ozon_site_price", sa.Numeric(10, 2)),
    ("ozon_site_price_at", sa.DateTime()),
    ("ozon_site_seller_price", sa.Numeric(10, 2)),
]

SNAPSHOT_COLUMNS = [
    ("ozon_site_price", sa.Numeric(10, 2)),
]


def _existing(table: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    for table, columns in (("product_config", CONFIG_COLUMNS),
                           ("price_snapshots", SNAPSHOT_COLUMNS)):
        existing = _existing(table)
        for name, type_ in columns:
            if name not in existing:
                op.add_column(table, sa.Column(name, type_, nullable=True))


def downgrade():
    for table, columns in (("price_snapshots", SNAPSHOT_COLUMNS),
                           ("product_config", CONFIG_COLUMNS)):
        existing = _existing(table)
        for name, _ in reversed(columns):
            if name in existing:
                op.drop_column(table, name)
