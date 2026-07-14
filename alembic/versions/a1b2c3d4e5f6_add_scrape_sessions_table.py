"""add scrape_sessions table

Revision ID: a1b2c3d4e5f6
Revises: 5654661f6eb5
Create Date: 2026-03-16 13:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '5654661f6eb5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scrape_sessions",
        sa.Column("id",                  sa.Integer,      primary_key=True),
        sa.Column("uuid",                sa.String(36),   nullable=False, unique=True),
        sa.Column("query",               sa.String(500),  nullable=False),
        sa.Column("category",            sa.String(255),  nullable=True),
        sa.Column("city",                sa.String(255),  nullable=True),
        sa.Column("state",               sa.String(255),  nullable=True),
        sa.Column("country",             sa.String(255),  nullable=True),
        sa.Column("total_scraped",       sa.Integer(),    nullable=False, default=0),
        sa.Column("total_saved",         sa.Integer(),    nullable=False, default=0),
        sa.Column("skipped_duplicates",  sa.Integer(),    nullable=False, default=0),
        sa.Column("started_at",          sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at",         sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec",        sa.Float(),      nullable=True),
        sa.Column("status",              sa.String(50),   nullable=False, default="running"),
        sa.Column("error",               sa.String(1000), nullable=True),
        sa.Column("created_at",          sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("scrape_sessions")
