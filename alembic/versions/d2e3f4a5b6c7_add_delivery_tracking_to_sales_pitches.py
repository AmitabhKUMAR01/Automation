"""add_delivery_tracking_to_sales_pitches

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-02

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sales_pitches', sa.Column(
        'delivery_status', sa.String(length=20), nullable=True, server_default='pending'
    ))
    op.add_column('sales_pitches', sa.Column(
        'delivered_at', sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column('sales_pitches', sa.Column(
        'delivery_error', sa.Text(), nullable=True
    ))
    op.add_column('sales_pitches', sa.Column(
        'delivery_attempts', sa.Integer(), nullable=False, server_default='0'
    ))


def downgrade() -> None:
    op.drop_column('sales_pitches', 'delivery_attempts')
    op.drop_column('sales_pitches', 'delivery_error')
    op.drop_column('sales_pitches', 'delivered_at')
    op.drop_column('sales_pitches', 'delivery_status')
