"""add brand to products

Revision ID: 20260428_0001_010
Revises: 009_add_composite_indexes
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '20260428_0001_010'
down_revision = '009_add_composite_indexes'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    col_exists = bind.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='products' AND column_name='brand'"
    )).fetchone()
    if not col_exists:
        op.add_column('products', sa.Column('brand', sa.String(255), nullable=True, comment='Бренд товара'))
    idx_exists = bind.execute(text(
        "SELECT 1 FROM pg_indexes WHERE tablename='products' AND indexname='ix_products_brand'"
    )).fetchone()
    if not idx_exists:
        op.create_index('ix_products_brand', 'products', ['brand'])


def downgrade():
    op.drop_index('ix_products_brand', 'products')
    op.drop_column('products', 'brand')
