"""add is_test_run to application_run

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_run", sa.Column("is_test_run", sa.Boolean, server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("application_run", "is_test_run")
