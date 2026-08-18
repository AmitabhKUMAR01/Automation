"""add last_search_used_at to profile_settings

Revision ID: l1m2n3o4p5q6
Revises: k1l2m3n4o5p6
Create Date: 2026-08-18

Adds a dedicated rotation timestamp `last_search_used_at` to profile_settings.

This column is updated ONLY by the daily_linkedin_search job, keeping its
per-profile rotation independent from other jobs (acceptance check, pitch
delivery, etc.) that also call _run_job_for_profiles and update last_used_at.

Without this column, all profiles end up with the same last_used_at after
other jobs run, causing daily_linkedin_search to always pick the same profile
(lowest id) instead of rotating correctly.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'l1m2n3o4p5q6'
down_revision = 'k1l2m3n4o5p6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'profile_settings',
        sa.Column('last_search_used_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('profile_settings', 'last_search_used_at')
