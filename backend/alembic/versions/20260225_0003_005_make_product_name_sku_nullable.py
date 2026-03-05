"""005 make product name and seller_sku nullable

Revision ID: 005_nullable_sku_name
Revises: 004_product_image_url
Create Date: 2026-02-25
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '005_nullable_sku_name'
down_revision = '004_product_image_url'
branch_labels = None
depends_on = None


def upgrade():
    # Делаем seller_sku и name nullable
    # (название и артикул продавца теперь могут подтягиваться автоматически из WB API)
    op.alter_column(
        'products', 'seller_sku',
        existing_type=sa.String(length=255),
        nullable=True
    )
    op.alter_column(
        'products', 'name',
        existing_type=sa.String(length=500),
        nullable=True
    )


def downgrade():
    # При откате — заполняем NULL значения заглушкой, затем ставим NOT NULL обратно
    op.execute("UPDATE products SET seller_sku = CAST(nm_id AS TEXT) WHERE seller_sku IS NULL")
    op.execute("UPDATE products SET name = '' WHERE name IS NULL")

    op.alter_column(
        'products', 'seller_sku',
        existing_type=sa.String(length=255),
        nullable=False
    )
    op.alter_column(
        'products', 'name',
        existing_type=sa.String(length=500),
        nullable=False
    )
