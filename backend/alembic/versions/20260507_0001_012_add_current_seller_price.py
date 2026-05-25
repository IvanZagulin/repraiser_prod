"""add current_seller_price to products

Revision ID: 20260507_0001_012
Revises: 20260506_0001_011
Create Date: 2026-05-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '20260507_0001_012'
down_revision = '20260506_0001_011'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Добавляем колонку
    col_exists = bind.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='products' AND column_name='current_seller_price'"
    )).fetchone()
    if not col_exists:
        op.add_column('products', sa.Column(
            'current_seller_price',
            sa.Numeric(10, 2),
            nullable=True,
            comment='Актуальная цена продавца (денормализация из последнего снапшота)'
        ))

    # Заполняем из последнего снапшота для каждого товара
    bind.execute(text("""
        UPDATE products p
        SET current_seller_price = s.seller_price
        FROM (
            SELECT DISTINCT ON (product_id) product_id, seller_price
            FROM price_snapshots
            WHERE seller_price IS NOT NULL
            ORDER BY product_id, snapshot_at DESC
        ) s
        WHERE p.id = s.product_id
          AND p.current_seller_price IS NULL
    """))


def downgrade():
    op.drop_column('products', 'current_seller_price')
