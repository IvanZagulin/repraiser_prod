"""Add image_url to products table

Revision ID: 004_product_image_url
Revises: 003_rrp_consider_wallet
Create Date: 2026-02-25 00:02:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '004_product_image_url'
down_revision = '003_rrp_consider_wallet'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'products',
        sa.Column(
            'image_url',
            sa.String(length=500),
            nullable=True,
            comment='URL фото товара из WB Content API'
        )
    )


def downgrade() -> None:
    op.drop_column('products', 'image_url')
