"""add specialties JSONB column to job_posting with GIN index

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_posting", sa.Column("specialties", JSONB, nullable=True))
    op.create_index(
        "ix_job_posting_specialties",
        "job_posting",
        ["specialties"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_job_posting_specialties", table_name="job_posting")
    op.drop_column("job_posting", "specialties")
