"""add_linkedin_contact_id_and_pitch_channel_to_sales_pitches

Revision ID: c1d2e3f4a5b6
Revises: b42ce37248fe
Create Date: 2026-07-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b42ce37248fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sales_pitches',
        sa.Column('linkedin_contact_id', sa.Integer(), nullable=True)
    )
    op.create_index(
        'ix_sales_pitches_linkedin_contact_id',
        'sales_pitches',
        ['linkedin_contact_id'],
        unique=False,
    )
    op.create_foreign_key(
        'fk_sales_pitches_linkedin_contact_id',
        'sales_pitches', 'linkedin_contacts',
        ['linkedin_contact_id'], ['id'],
        ondelete='SET NULL',
    )
    op.add_column(
        'sales_pitches',
        sa.Column('pitch_channel', sa.String(length=20), nullable=True, server_default='generic')
    )


def downgrade() -> None:
    op.drop_column('sales_pitches', 'pitch_channel')
    op.drop_constraint('fk_sales_pitches_linkedin_contact_id', 'sales_pitches', type_='foreignkey')
    op.drop_index('ix_sales_pitches_linkedin_contact_id', table_name='sales_pitches')
    op.drop_column('sales_pitches', 'linkedin_contact_id')
