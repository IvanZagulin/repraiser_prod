"""add leads table

Revision ID: 20260506_0001_011
Revises: 20260428_0001_010
Create Date: 2026-05-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '20260506_0001_011'
down_revision = '20260428_0001_010'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    table_exists = bind.execute(text(
        "SELECT 1 FROM information_schema.tables WHERE table_name='leads'"
    )).fetchone()
    if not table_exists:
        op.create_table(
            'leads',
            sa.Column('id',         sa.Integer(),     primary_key=True, index=True),
            sa.Column('name',       sa.String(255),   nullable=False),
            sa.Column('email',      sa.String(255),   nullable=False, index=True),
            sa.Column('phone',      sa.String(50),    nullable=True),
            sa.Column('source',     sa.String(100),   nullable=True,  server_default='landing'),
            sa.Column('status',     sa.String(50),    nullable=False, server_default='new'),
            sa.Column('notes',      sa.Text(),        nullable=True),
            sa.Column('created_at', sa.DateTime(),    server_default=sa.func.now(), nullable=False),
        )


def downgrade():
    op.drop_table('leads')
