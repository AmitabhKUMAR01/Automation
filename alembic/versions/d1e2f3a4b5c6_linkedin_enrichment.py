"""linkedin_enrichment_add_contacts_and_flags

Revision ID: d1e2f3a4b5c6
Revises: cc45e8bf3bbb
Create Date: 2026-06-29 10:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "e53ae437ef86"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── 1. Create linkedin_contacts table ─────────────────────────────────────
    op.create_table(
        "linkedin_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("business_client_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("job_title", sa.String(255), nullable=True),
        sa.Column("profile_url", sa.String(500), nullable=False),
        sa.Column(
            "match_confidence", sa.String(10), nullable=True, server_default="medium"
        ),
        sa.Column(
            "connection_sent", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("connection_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            onupdate=sa.func.now(),
            nullable=True,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["business_client_id"],
            ["business_clients.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_linkedin_contacts_uuid", "linkedin_contacts", ["uuid"], unique=True
    )
    op.create_index(
        "ix_linkedin_contacts_business_client_id",
        "linkedin_contacts",
        ["business_client_id"],
        unique=False,
    )

    # ── 2. Add LinkedIn search-tracking columns to business_clients ───────────
    op.add_column(
        "business_clients",
        sa.Column(
            "is_linkedin_searched",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "business_clients",
        sa.Column(
            "linkedin_search_status",
            sa.String(50),
            nullable=True,
            server_default="pending",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("business_clients", "linkedin_search_status")
    op.drop_column("business_clients", "is_linkedin_searched")
    op.drop_index(
        "ix_linkedin_contacts_business_client_id", table_name="linkedin_contacts"
    )
    op.drop_index("ix_linkedin_contacts_uuid", table_name="linkedin_contacts")
    op.drop_table("linkedin_contacts")
