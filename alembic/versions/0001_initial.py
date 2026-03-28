"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_posting",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.String, nullable=False, unique=True),
        sa.Column("title", sa.String),
        sa.Column("company", sa.String),
        sa.Column("description", sa.Text),
        sa.Column("ats_type", sa.String, nullable=False, server_default="greenhouse"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "application_run",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_posting_id", UUID(as_uuid=True), sa.ForeignKey("job_posting.id"), nullable=False),
        sa.Column("job_url", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="PENDING"),
        sa.Column("current_state", sa.String),
        sa.Column("temporal_workflow_id", sa.String),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "application_step",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("application_run.id"), nullable=False),
        sa.Column("step_name", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="PENDING"),
        sa.Column("logs", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "application_artifact",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("application_run.id"), nullable=False),
        sa.Column("artifact_type", sa.String, nullable=False),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("artifact_metadata", JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "applicant_profile",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("full_name", sa.String, nullable=False),
        sa.Column("email", sa.String, nullable=False),
        sa.Column("phone", sa.String, nullable=False),
        sa.Column("location", sa.String, nullable=False),
        sa.Column("linkedin_url", sa.String),
        sa.Column("github_url", sa.String),
        sa.Column("portfolio_url", sa.String),
        sa.Column("resume_path", sa.String, nullable=False),
        sa.Column("years_of_experience", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skills", JSON, nullable=False, server_default="[]"),
        sa.Column("work_experience", JSON, nullable=False, server_default="[]"),
        sa.Column("education", JSON, nullable=False, server_default="[]"),
        sa.Column("bio", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("applicant_profile")
    op.drop_table("application_artifact")
    op.drop_table("application_step")
    op.drop_table("application_run")
    op.drop_table("job_posting")
