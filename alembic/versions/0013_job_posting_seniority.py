"""add seniority_level and experience_required to job_posting

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_posting", sa.Column("seniority_level", sa.String, nullable=True))
    op.add_column("job_posting", sa.Column("experience_required", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("job_posting", "experience_required")
    op.drop_column("job_posting", "seniority_level")
