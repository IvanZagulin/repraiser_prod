"""024 add ozon closed-loop state to product_config

Revision ID: 20261015_0003_024
Revises: 20261015_0002_023
Create Date: 2026-10-15 16:00:00.000000

Замкнутый цикл для Ozon-РРЦ: храним реально сложившийся коэффициент
доинвестирования per-product и доводим цену по нему. Все поля nullable —
для WB-товаров пустые.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261015_0003_024"
down_revision = "20261015_0002_023"
branch_labels = None
depends_on = None


COLUMNS = [
    ("ozon_realized_k", sa.Numeric(6, 4)),
    ("ozon_k_updated_at", sa.DateTime()),
    ("ozon_storefront_factor", sa.Numeric(6, 4)),
]


def _existing() -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("product_config")}


def upgrade():
    existing = _existing()
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column("product_config", sa.Column(name, type_, nullable=True))


def downgrade():
    existing = _existing()
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("product_config", name)
