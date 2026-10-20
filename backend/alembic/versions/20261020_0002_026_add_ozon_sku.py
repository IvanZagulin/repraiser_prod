"""026 add ozon_sku to products

Revision ID: 20261020_0002_026
Revises: 20261020_0001_025
Create Date: 2026-10-20 12:30:00.000000

У товара Ozon два разных номера. `nm_id` хранит внутренний product_id — им
ходим в Seller API. В адресе карточки стоит другой номер, публичный SKU, и
подставить вместо него product_id нельзя: Ozon откроет чужой товар (проверено
на «Альтернативе» — product_id 3550387955 открывает ротор для болгарки).
Поэтому SKU храним отдельно, иначе ссылку на карточку не собрать.

Колонка nullable — для WB-товаров она пустая.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261020_0002_026"
down_revision = "20261020_0001_025"
branch_labels = None
depends_on = None


def _existing() -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("products")}


def upgrade():
    if "ozon_sku" not in _existing():
        op.add_column("products", sa.Column("ozon_sku", sa.BigInteger(), nullable=True))


def downgrade():
    if "ozon_sku" in _existing():
        op.drop_column("products", "ozon_sku")
