"""023 add ozon price indexes and buyer price to price_snapshots

Revision ID: 20261015_0002_023
Revises: 20261015_0001_022
Create Date: 2026-10-15 12:00:00.000000

Ozon не отдаёт цену покупателя напрямую, но её можно восстановить из ценовых
индексов (см. app/services/ozon_buyer_price.py). Складываем в снапшот и сами
индексы, и расчётную цену, и коэффициент доинвестирования — чтобы потом видеть
их динамику по товару, а не только текущее значение.

Все колонки nullable: для WB-товаров они остаются пустыми.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261015_0002_023"
down_revision = "20261015_0001_022"
branch_labels = None
depends_on = None


COLUMNS = [
    ("old_price", sa.Numeric(10, 2)),
    ("min_price", sa.Numeric(10, 2)),
    ("ozon_competitor_min_price", sa.Numeric(10, 2)),
    ("ozon_price_index", sa.Numeric(6, 3)),
    ("external_competitor_min_price", sa.Numeric(10, 2)),
    ("external_price_index", sa.Numeric(6, 3)),
    ("self_mp_min_price", sa.Numeric(10, 2)),
    ("self_mp_price_index", sa.Numeric(6, 3)),
    ("buyer_price_estimate", sa.Numeric(10, 2)),
    ("buyer_price_spread_pct", sa.Numeric(6, 2)),
    ("buyer_price_sources", sa.Integer()),
    ("coinvest_ratio", sa.Numeric(6, 4)),
    ("commission_percent", sa.Numeric(5, 2)),
]


def _existing_columns() -> set:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns("price_snapshots")}


def upgrade():
    existing = _existing_columns()
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column("price_snapshots", sa.Column(name, type_, nullable=True))


def downgrade():
    existing = _existing_columns()
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("price_snapshots", name)
