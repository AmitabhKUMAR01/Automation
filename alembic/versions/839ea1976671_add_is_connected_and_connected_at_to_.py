"""add_is_connected_and_connected_at_to_linkedin_contacts

Revision ID: 839ea1976671
Revises: d1e2f3a4b5c6
Create Date: 2026-06-30 18:40:03.258247

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '839ea1976671'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'linkedin_contacts',
        sa.Column('is_connected', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'linkedin_contacts',
        sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('linkedin_contacts', 'connected_at')
    op.drop_column('linkedin_contacts', 'is_connected')
