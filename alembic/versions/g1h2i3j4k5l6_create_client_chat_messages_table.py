"""create client chat messages table

Revision ID: g1h2i3j4k5l6
Revises: b2a6ef3b22d2
Create Date: 2026-07-29

Creates client_chat_messages table for storing client conversation history,
LLM suggested replies, profile_id, and Telegram webhook chat_id.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'g1h2i3j4k5l6'
down_revision = 'b2a6ef3b22d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'client_chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('profile_id', sa.Integer(), nullable=True),
        sa.Column('sales_pitch_id', sa.Integer(), nullable=True),
        sa.Column('linkedin_contact_id', sa.Integer(), nullable=True),
        sa.Column('linkedin_search_contact_id', sa.Integer(), nullable=True),
        sa.Column('sender', sa.String(length=50), nullable=False),
        sa.Column('message_body', sa.Text(), nullable=False),
        sa.Column('is_self', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('suggested_reply', sa.Text(), nullable=True),
        sa.Column('telegram_chat_id', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True, server_default='received'),
        sa.Column('sent_reply_text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['profile_id'], ['profile_settings.id']),
        sa.ForeignKeyConstraint(['sales_pitch_id'], ['sales_pitches.id']),
        sa.ForeignKeyConstraint(['linkedin_contact_id'], ['linkedin_contacts.id']),
        sa.ForeignKeyConstraint(['linkedin_search_contact_id'], ['linkedin_search_contacts.id']),
    )
    op.create_index(op.f('ix_client_chat_messages_uuid'), 'client_chat_messages', ['uuid'], unique=True)
    op.create_index(op.f('ix_client_chat_messages_profile_id'), 'client_chat_messages', ['profile_id'], unique=False)
    op.create_index(op.f('ix_client_chat_messages_sales_pitch_id'), 'client_chat_messages', ['sales_pitch_id'], unique=False)
    op.create_index(op.f('ix_client_chat_messages_linkedin_contact_id'), 'client_chat_messages', ['linkedin_contact_id'], unique=False)
    op.create_index(op.f('ix_client_chat_messages_linkedin_search_contact_id'), 'client_chat_messages', ['linkedin_search_contact_id'], unique=False)
    op.create_index(op.f('ix_client_chat_messages_telegram_chat_id'), 'client_chat_messages', ['telegram_chat_id'], unique=False)
    op.create_index(op.f('ix_client_chat_messages_status'), 'client_chat_messages', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_client_chat_messages_status'), table_name='client_chat_messages')
    op.drop_index(op.f('ix_client_chat_messages_telegram_chat_id'), table_name='client_chat_messages')
    op.drop_index(op.f('ix_client_chat_messages_linkedin_search_contact_id'), table_name='client_chat_messages')
    op.drop_index(op.f('ix_client_chat_messages_linkedin_contact_id'), table_name='client_chat_messages')
    op.drop_index(op.f('ix_client_chat_messages_sales_pitch_id'), table_name='client_chat_messages')
    op.drop_index(op.f('ix_client_chat_messages_profile_id'), table_name='client_chat_messages')
    op.drop_index(op.f('ix_client_chat_messages_uuid'), table_name='client_chat_messages')
    op.drop_table('client_chat_messages')
