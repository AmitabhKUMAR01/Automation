"""add_email_to_business_clients

Revision ID: b42ce37248fe
Revises: 333e2ce0fe0f
Create Date: 2026-07-01 18:16:44.391163

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b42ce37248fe'
down_revision: Union[str, Sequence[str], None] = '333e2ce0fe0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'business_clients',
        sa.Column('email', sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('business_clients', 'email')
