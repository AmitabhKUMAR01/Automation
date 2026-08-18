"""add location to profile_settings

Revision ID: k1l2m3n4o5p6
Revises: j1k2l3m4n5o6
Create Date: 2026-08-18

Adds a nullable VARCHAR(255) column `location` to profile_settings.

Each profile can now target a different LinkedIn search location
(e.g. "Bahrain", "Dubai"). When NULL, the job falls back to the
global LinkedinSearchConfig.location value.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'k1l2m3n4o5p6'
down_revision = 'j1k2l3m4n5o6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'profile_settings',
        sa.Column('location', sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('profile_settings', 'location')
