"""add exported_at to linkedin_search_contacts

Revision ID: i1j2k3l4m5n6
Revises: h1i2j3k4l5m6
Create Date: 2026-08-04

Adds a nullable exported_at column to linkedin_search_contacts.
NULL means the row has never been exported to Google Sheets.
A timestamp means it was exported at that UTC time.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'i1j2k3l4m5n6'
down_revision = 'h1i2j3k4l5m6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'linkedin_search_contacts',
        sa.Column('exported_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Index for fast "un-exported rows" queries
    op.create_index(
        'ix_linkedin_search_contacts_exported_at',
        'linkedin_search_contacts',
        ['exported_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_linkedin_search_contacts_exported_at', table_name='linkedin_search_contacts')
    op.drop_column('linkedin_search_contacts', 'exported_at')
