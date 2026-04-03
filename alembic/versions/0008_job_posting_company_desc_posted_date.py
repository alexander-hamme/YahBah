"""add company_description and posted_date to job_posting

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_posting", sa.Column("company_description", sa.String, nullable=True))
    op.add_column("job_posting", sa.Column("posted_date", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("job_posting", "posted_date")
    op.drop_column("job_posting", "company_description")
