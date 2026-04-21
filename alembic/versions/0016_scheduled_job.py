"""add scheduled_job table for job alert email parsing

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_job",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_url", sa.String, nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=True),
        sa.Column("company", sa.String, nullable=True),
        sa.Column("location", sa.String, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("snippet", sa.Text, nullable=True),
        sa.Column("match_score", sa.Integer, nullable=True),
        sa.Column("match_rationale", sa.Text, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="SCHEDULED"),
        sa.Column("hold", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("gmail_message_id", sa.String, nullable=False),
        sa.Column("promote_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promoted_run_id", PG_UUID(as_uuid=True), sa.ForeignKey("application_run.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scheduled_job_status", "scheduled_job", ["status"])
    op.create_index("ix_scheduled_job_promote_after", "scheduled_job", ["promote_after"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_job_promote_after")
    op.drop_index("ix_scheduled_job_status")
    op.drop_table("scheduled_job")
