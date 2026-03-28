"""add account credentials to application_run

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_run", sa.Column("account_email", sa.String))
    op.add_column("application_run", sa.Column("account_password", sa.String))


def downgrade() -> None:
    op.drop_column("application_run", "account_password")
    op.drop_column("application_run", "account_email")
