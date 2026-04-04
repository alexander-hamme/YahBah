"""add industry column to job_posting

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_posting", sa.Column("industry", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("job_posting", "industry")
