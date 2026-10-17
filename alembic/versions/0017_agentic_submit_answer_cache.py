"""agentic pipeline: submit/confirmation tracking on application_run, answer_cache table

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_run", sa.Column("submit_attempted_at", sa.DateTime(timezone=True)))
    op.add_column("application_run", sa.Column("email_confirmed_at", sa.DateTime(timezone=True)))
    op.add_column("application_run", sa.Column("email_confirmed_message_id", sa.String))
    op.create_table(
        "answer_cache",
        sa.Column("question_key", sa.String, primary_key=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("options", JSONB, server_default="[]", nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("hits", sa.Integer, server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("answer_cache")
    op.drop_column("application_run", "email_confirmed_message_id")
    op.drop_column("application_run", "email_confirmed_at")
    op.drop_column("application_run", "submit_attempted_at")
