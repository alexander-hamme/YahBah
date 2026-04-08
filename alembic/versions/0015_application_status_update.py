"""add application_status_update table for email status tracking

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_status_update",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", PG_UUID(as_uuid=True), sa.ForeignKey("application_run.id"), nullable=False),
        sa.Column("status_type", sa.String, nullable=False),
        sa.Column("subject", sa.String, nullable=True),
        sa.Column("sender", sa.String, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("gmail_message_id", sa.String, nullable=False, unique=True),
        sa.Column("raw_snippet", sa.Text, nullable=True),
        sa.Column("email_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_application_status_update_run_id", "application_status_update", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_application_status_update_run_id")
    op.drop_table("application_status_update")
