"""add reply tracking to sales pitches

Revision ID: e1f2a3b4c5d6
Revises: d2e3f4a5b6c7
Create Date: 2026-07-06

Adds four columns to sales_pitches to track whether a LinkedIn DM pitch
received a reply:
  - reply_received   (Boolean, default False)
  - reply_text       (Text, nullable)
  - replied_at       (DateTime with tz, nullable)
  - reply_checked_at (DateTime with tz, nullable)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1f2a3b4c5d6'
down_revision = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'sales_pitches',
        sa.Column('reply_received', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'sales_pitches',
        sa.Column('reply_text', sa.Text(), nullable=True),
    )
    op.add_column(
        'sales_pitches',
        sa.Column('replied_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'sales_pitches',
        sa.Column('reply_checked_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('sales_pitches', 'reply_checked_at')
    op.drop_column('sales_pitches', 'replied_at')
    op.drop_column('sales_pitches', 'reply_text')
    op.drop_column('sales_pitches', 'reply_received')
