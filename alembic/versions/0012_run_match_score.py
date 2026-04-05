"""add match_score and match_rationale to application_run

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_run", sa.Column("match_score", sa.Integer, nullable=True))
    op.add_column("application_run", sa.Column("match_rationale", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("application_run", "match_rationale")
    op.drop_column("application_run", "match_score")
