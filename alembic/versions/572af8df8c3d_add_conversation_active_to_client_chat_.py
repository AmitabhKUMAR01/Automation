"""add_conversation_active_to_client_chat_message

Revision ID: 572af8df8c3d
Revises: i1j2k3l4m5n6
Create Date: 2026-08-06 13:41:55.929717

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '572af8df8c3d'
down_revision: Union[str, Sequence[str], None] = 'i1j2k3l4m5n6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add conversation_active column to client_chat_messages table.

    This flag is set to True when we send a reply (via auto-send or Telegram
    approval), marking the thread as open and eligible for the follow-up reply
    checker to re-scan for subsequent client messages.
    """
    op.add_column(
        'client_chat_messages',
        sa.Column(
            'conversation_active',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Remove conversation_active column from client_chat_messages table."""
    op.drop_column('client_chat_messages', 'conversation_active')
