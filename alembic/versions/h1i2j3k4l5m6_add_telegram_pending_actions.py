"""add telegram_pending_actions table

Revision ID: h1i2j3k4l5m6
Revises: g1h2i3j4k5l6
Create Date: 2026-07-31

Creates telegram_pending_actions table used by the cloud bridge microservice
to queue incoming Telegram bot replies for processing by the local PC poller.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'h1i2j3k4l5m6'
down_revision = 'g1h2i3j4k5l6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'telegram_pending_actions',
        sa.Column('id',            sa.Integer(),                  nullable=False),
        sa.Column('chat_id',       sa.String(length=100),         nullable=True),
        sa.Column('pitch_id',      sa.Integer(),                  nullable=True),
        sa.Column('profile_id',    sa.Integer(),                  nullable=True),
        sa.Column('action',        sa.Text(),                     nullable=False),
        # status lifecycle: pending → processing → done / failed
        sa.Column('status',        sa.String(length=20),          nullable=False, server_default='pending'),
        sa.Column('raw_payload',   sa.JSON(),                     nullable=True),
        sa.Column('error_message', sa.Text(),                     nullable=True),
        sa.Column('created_at',    sa.DateTime(timezone=True),    server_default=sa.func.now(), nullable=False),
        sa.Column('processed_at',  sa.DateTime(timezone=True),    nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_telegram_pending_actions_id'),       'telegram_pending_actions', ['id'],       unique=False)
    op.create_index(op.f('ix_telegram_pending_actions_status'),   'telegram_pending_actions', ['status'],   unique=False)
    op.create_index(op.f('ix_telegram_pending_actions_chat_id'),  'telegram_pending_actions', ['chat_id'],  unique=False)
    op.create_index(op.f('ix_telegram_pending_actions_pitch_id'), 'telegram_pending_actions', ['pitch_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_telegram_pending_actions_pitch_id'), table_name='telegram_pending_actions')
    op.drop_index(op.f('ix_telegram_pending_actions_chat_id'),  table_name='telegram_pending_actions')
    op.drop_index(op.f('ix_telegram_pending_actions_status'),   table_name='telegram_pending_actions')
    op.drop_index(op.f('ix_telegram_pending_actions_id'),       table_name='telegram_pending_actions')
    op.drop_table('telegram_pending_actions')
