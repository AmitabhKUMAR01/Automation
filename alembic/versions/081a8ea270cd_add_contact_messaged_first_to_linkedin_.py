"""add_contact_messaged_first_to_linkedin_search_contacts

Revision ID: 081a8ea270cd
Revises: 572af8df8c3d
Create Date: 2026-08-11 12:11:34.289656

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '081a8ea270cd'
down_revision: Union[str, Sequence[str], None] = '572af8df8c3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add contact_messaged_first column to linkedin_search_contacts."""
    op.add_column(
        'linkedin_search_contacts',
        sa.Column(
            'contact_messaged_first',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('0'),
        ),
    )


def downgrade() -> None:
    """Remove contact_messaged_first column from linkedin_search_contacts."""
    op.drop_column('linkedin_search_contacts', 'contact_messaged_first')
