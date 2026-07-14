"""create business_client table

Revision ID: 59c5914d0ed3
Revises: 
Create Date: 2026-03-11 15:55:44.174807

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59c5914d0ed3'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "business_clients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("uuid", sa.String(36), nullable=False,unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category",sa.String(255),nullable=True),
        sa.Column("url",sa.String(255),nullable=True),
        sa.Column("phone", sa.String(20),nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("is_approached", sa.Boolean(), default=False),
        sa.Column("scrape_source", sa.String(500), nullable=True),
        sa.Column("social_links", sa.JSON(), nullable=True),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.Column("country", sa.String(255), nullable=True),
        sa.Column("state", sa.String(255), nullable=True),
        sa.Column("city", sa.String(255), nullable=True),
        sa.Column("zip_code", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("business_clients")

