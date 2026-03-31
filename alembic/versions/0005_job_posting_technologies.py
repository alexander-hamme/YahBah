"""add technologies JSONB column to job_posting with GIN index

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_posting", sa.Column("technologies", JSONB, nullable=True))
    op.create_index(
        "ix_job_posting_technologies",
        "job_posting",
        ["technologies"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_job_posting_technologies", table_name="job_posting")
    op.drop_column("job_posting", "technologies")
