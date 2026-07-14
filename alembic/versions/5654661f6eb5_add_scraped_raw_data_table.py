"""add scraped_raw_data table

Revision ID: 5654661f6eb5
Revises: 59c5914d0ed3
Create Date: 2026-03-16 12:55:23.317896

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5654661f6eb5'
down_revision: Union[str, Sequence[str], None] = '59c5914d0ed3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scraped_raw_data",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("uuid", sa.String(36), nullable=False,unique=True),
        sa.Column("business_client_id", sa.Integer, nullable=False, index=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("browser",       sa.String(255), nullable=True),
        sa.Column("search_engine", sa.String(255), nullable=True),
        sa.Column("scrape_source", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["business_client_id"], ["business_clients.id"]),
    )


def downgrade() -> None:
    op.drop_table("scraped_raw_data")
