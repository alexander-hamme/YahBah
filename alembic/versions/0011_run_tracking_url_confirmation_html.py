"""add tracking_url and confirmation_html to application_run

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_run", sa.Column("tracking_url", sa.String, nullable=True))
    op.add_column("application_run", sa.Column("confirmation_html", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("application_run", "confirmation_html")
    op.drop_column("application_run", "tracking_url")
