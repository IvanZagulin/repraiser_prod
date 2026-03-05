"""Initial migration - create all tables

Revision ID: 001_initial
Revises: 
Create Date: 2026-02-17 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Accounts table
    op.create_table('accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False, comment='Название аккаунта'),
        sa.Column('wb_api_token', sa.Text(), nullable=False, comment='JWT токен WB API'),
        sa.Column('token_expires_at', sa.DateTime(), nullable=True, comment='Срок действия токена'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true', comment='Активен ли аккаунт'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_accounts_id'), 'accounts', ['id'], unique=False)

    # Products table
    op.create_table('products',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('nm_id', sa.BigInteger(), nullable=False, comment='Артикул WB (nmID)'),
        sa.Column('seller_sku', sa.String(length=255), nullable=False, comment='Артикул продавца'),
        sa.Column('name', sa.String(length=500), nullable=False, comment='Название товара'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_products_id'), 'products', ['id'], unique=False)
    op.create_index(op.f('ix_products_nm_id'), 'products', ['nm_id'], unique=False)
    op.create_index(op.f('ix_products_seller_sku'), 'products', ['seller_sku'], unique=False)

    # Product Config table
    op.create_table('product_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('strategy', sa.String(length=20), nullable=False, comment='Стратегия: rrp или competitor'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true', comment='Включен ли репрайсер'),
        sa.Column('min_price', sa.Numeric(precision=10, scale=2), nullable=False, comment='Минимальная цена (0 для отключения)'),
        sa.Column('max_price', sa.Numeric(precision=10, scale=2), nullable=True, comment='Максимальная цена'),
        sa.Column('rrp_price', sa.Numeric(precision=10, scale=2), nullable=True, comment='Рекомендованная розничная цена'),
        sa.Column('rrp_tolerance', sa.Numeric(precision=10, scale=2), nullable=True, server_default='5', comment='Допуск для РРЦ (в рублях)'),
        sa.Column('competitor_nm_id', sa.BigInteger(), nullable=True, comment='nmID конкурента'),
        sa.Column('competitor_offset_type', sa.String(length=5), nullable=True, comment='Тип смещения: rub или pct'),
        sa.Column('competitor_offset_direction', sa.String(length=6), nullable=True, comment='Направление: above или below'),
        sa.Column('competitor_offset_value', sa.Numeric(precision=10, scale=2), nullable=True, comment='Величина смещения'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.CheckConstraint("strategy IN ('rrp', 'competitor')", name='check_strategy'),
        sa.CheckConstraint('min_price >= 0', name='check_min_price_non_negative'),
        sa.CheckConstraint('max_price IS NULL OR max_price >= min_price', name='check_max_price'),
        sa.CheckConstraint("competitor_offset_type IS NULL OR competitor_offset_type IN ('rub', 'pct')", name='check_offset_type'),
        sa.CheckConstraint("competitor_offset_direction IS NULL OR competitor_offset_direction IN ('above', 'below')", name='check_offset_direction'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id')
    )
    op.create_index(op.f('ix_product_config_id'), 'product_config', ['id'], unique=False)

    # Repricer Runs table
    op.create_table('repricer_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_type', sa.String(length=20), nullable=False, comment='Тип: scheduled или manual'),
        sa.Column('total_products', sa.Integer(), server_default='0', comment='Всего товаров обработано'),
        sa.Column('updated_count', sa.Integer(), server_default='0', comment='Цены изменены'),
        sa.Column('skipped_count', sa.Integer(), server_default='0', comment='Пропущено'),
        sa.Column('error_count', sa.Integer(), server_default='0', comment='Ошибок'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='running', comment='Статус: running, completed, failed'),
        sa.Column('error_message', sa.Text(), nullable=True, comment='Сообщение об ошибке'),
        sa.Column('started_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_repricer_runs_id'), 'repricer_runs', ['id'], unique=False)
    op.create_index(op.f('ix_repricer_runs_started_at'), 'repricer_runs', ['started_at'], unique=False)

    # Price Snapshots table
    op.create_table('price_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('seller_price', sa.Numeric(precision=10, scale=2), nullable=False, comment='Цена продавца (basic)'),
        sa.Column('final_price', sa.Numeric(precision=10, scale=2), nullable=False, comment='Финальная цена покупателя (product)'),
        sa.Column('discount', sa.Numeric(precision=5, scale=2), nullable=True, comment='Скидка продавца (%)'),
        sa.Column('spp', sa.Numeric(precision=5, scale=2), nullable=True, comment='СПП (%)'),
        sa.Column('competitor_nm_id', sa.BigInteger(), nullable=True, comment='nmID конкурента'),
        sa.Column('competitor_price', sa.Numeric(precision=10, scale=2), nullable=True, comment='Цена конкурента'),
        sa.Column('competitor_in_stock', sa.Boolean(), nullable=True, comment='Наличие у конкурента'),
        sa.Column('is_in_promo', sa.Boolean(), nullable=False, server_default='false', comment='Участвует в акции WB'),
        sa.Column('total_quantity', sa.Integer(), nullable=True, comment='Остаток на складах WB'),
        sa.Column('snapshot_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP'), comment='Время снимка'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_price_snapshots_id'), 'price_snapshots', ['id'], unique=False)
    op.create_index(op.f('ix_price_snapshots_product_id'), 'price_snapshots', ['product_id'], unique=False)
    op.create_index(op.f('ix_price_snapshots_snapshot_at'), 'price_snapshots', ['snapshot_at'], unique=False)

    # Price Changes table
    op.create_table('price_changes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('old_seller_price', sa.Numeric(precision=10, scale=2), nullable=False, comment='Старая цена продавца'),
        sa.Column('old_final_price', sa.Numeric(precision=10, scale=2), nullable=False, comment='Старая финальная цена'),
        sa.Column('new_seller_price', sa.Numeric(precision=10, scale=2), nullable=True, comment='Новая цена продавца'),
        sa.Column('new_final_price', sa.Numeric(precision=10, scale=2), nullable=True, comment='Новая финальная цена (расчётная)'),
        sa.Column('reason_code', sa.String(length=50), nullable=False, comment='Код причины'),
        sa.Column('reason_message', sa.Text(), nullable=True, comment='Подробное описание'),
        sa.Column('changed_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['run_id'], ['repricer_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_price_changes_id'), 'price_changes', ['id'], unique=False)
    op.create_index(op.f('ix_price_changes_product_id'), 'price_changes', ['product_id'], unique=False)
    op.create_index(op.f('ix_price_changes_run_id'), 'price_changes', ['run_id'], unique=False)
    op.create_index(op.f('ix_price_changes_changed_at'), 'price_changes', ['changed_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_price_changes_changed_at'), table_name='price_changes')
    op.drop_index(op.f('ix_price_changes_run_id'), table_name='price_changes')
    op.drop_index(op.f('ix_price_changes_product_id'), table_name='price_changes')
    op.drop_index(op.f('ix_price_changes_id'), table_name='price_changes')
    op.drop_table('price_changes')
    
    op.drop_index(op.f('ix_price_snapshots_snapshot_at'), table_name='price_snapshots')
    op.drop_index(op.f('ix_price_snapshots_product_id'), table_name='price_snapshots')
    op.drop_index(op.f('ix_price_snapshots_id'), table_name='price_snapshots')
    op.drop_table('price_snapshots')
    
    op.drop_index(op.f('ix_repricer_runs_started_at'), table_name='repricer_runs')
    op.drop_index(op.f('ix_repricer_runs_id'), table_name='repricer_runs')
    op.drop_table('repricer_runs')
    
    op.drop_index(op.f('ix_product_config_id'), table_name='product_config')
    op.drop_table('product_config')
    
    op.drop_index(op.f('ix_products_seller_sku'), table_name='products')
    op.drop_index(op.f('ix_products_nm_id'), table_name='products')
    op.drop_index(op.f('ix_products_id'), table_name='products')
    op.drop_table('products')
    
    op.drop_index(op.f('ix_accounts_id'), table_name='accounts')
    op.drop_table('accounts')
