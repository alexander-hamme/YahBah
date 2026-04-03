"""add company_website column to job_posting

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_posting", sa.Column("company_website", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("job_posting", "company_website")
