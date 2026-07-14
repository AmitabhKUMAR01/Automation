"""add_scheduler_job_runs_table

Revision ID: 52a3633bc563
Revises: a1b8f52c3c44
Create Date: 2026-07-09 14:20:51.595670

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '52a3633bc563'
down_revision: Union[str, Sequence[str], None] = 'a1b8f52c3c44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create scheduler_job_runs table."""
    op.create_table(
        'scheduler_job_runs',
        sa.Column('id',            sa.Integer(),                              nullable=False),
        sa.Column('job_id',        sa.String(length=100),                    nullable=False),
        sa.Column('job_name',      sa.String(length=255),                    nullable=False),
        sa.Column('status',        sa.String(length=50),                     nullable=False),
        sa.Column('started_at',    sa.DateTime(timezone=True),               nullable=False),
        sa.Column('finished_at',   sa.DateTime(timezone=True),               nullable=True),
        sa.Column('duration_sec',  sa.Float(),                               nullable=True),
        sa.Column('error_message', sa.String(length=1000),                   nullable=True),
        sa.Column('traceback',     sa.Text(),                                nullable=True),
        sa.Column('retry_count',   sa.Integer(),                             nullable=False, server_default='0'),
        sa.Column('created_at',    sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_scheduler_job_runs_id'),     'scheduler_job_runs', ['id'],     unique=False)
    op.create_index(op.f('ix_scheduler_job_runs_job_id'), 'scheduler_job_runs', ['job_id'], unique=False)


def downgrade() -> None:
    """Drop scheduler_job_runs table."""
    op.drop_index(op.f('ix_scheduler_job_runs_job_id'), table_name='scheduler_job_runs')
    op.drop_index(op.f('ix_scheduler_job_runs_id'),     table_name='scheduler_job_runs')
    op.drop_table('scheduler_job_runs')
