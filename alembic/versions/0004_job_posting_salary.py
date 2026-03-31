"""add salary_min and salary_max to job_posting

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_posting", sa.Column("salary_min", sa.Integer, nullable=True))
    op.add_column("job_posting", sa.Column("salary_max", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("job_posting", "salary_max")
    op.drop_column("job_posting", "salary_min")
