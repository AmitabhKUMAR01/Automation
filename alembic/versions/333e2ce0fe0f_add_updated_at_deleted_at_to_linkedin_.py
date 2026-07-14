"""add_updated_at_deleted_at_to_linkedin_contacts

Revision ID: 333e2ce0fe0f
Revises: 839ea1976671
Create Date: 2026-06-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '333e2ce0fe0f'
down_revision: Union[str, Sequence[str], None] = '839ea1976671'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('linkedin_contacts')]

    if 'updated_at' not in existing_columns:
        op.add_column(
            'linkedin_contacts',
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        )
    if 'deleted_at' not in existing_columns:
        op.add_column(
            'linkedin_contacts',
            sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column('linkedin_contacts', 'deleted_at')
    op.drop_column('linkedin_contacts', 'updated_at')
