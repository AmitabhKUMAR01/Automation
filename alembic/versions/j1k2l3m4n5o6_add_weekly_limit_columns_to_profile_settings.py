"""add weekly_limit_columns to profile_settings

Revision ID: j1k2l3m4n5o6
Revises: i1j2k3l4m5n6
Create Date: 2026-08-12

Adds two nullable DateTime columns to profile_settings to track the
LinkedIn weekly invite limit state per profile:

  - weekly_limit_reached_at : set to UTC now when LinkedIn's weekly cap is
                               detected for this profile. NULL = not blocked.
  - weekly_limit_resets_at  : pre-calculated next Monday 00:00 UTC.
                               The scheduler skips daily_linkedin_search for
                               this profile until this timestamp passes, then
                               auto-clears both columns back to NULL.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'j1k2l3m4n5o6'
down_revision = '081a8ea270cd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'profile_settings',
        sa.Column('weekly_limit_reached_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'profile_settings',
        sa.Column('weekly_limit_resets_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('profile_settings', 'weekly_limit_resets_at')
    op.drop_column('profile_settings', 'weekly_limit_reached_at')
