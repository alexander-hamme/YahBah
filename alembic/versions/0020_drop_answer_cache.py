"""drop answer_cache: the two-pass answer engine (llm/answers.py) answers from stored facts every run

Cached answers were keyed by question text and served ahead of the applicant's stored answers; the
engine has no cache, so an edited fact applies to the next plan. Downgrade restores the table as
0019 left it, empty (the dropped rows are regenerable and are not restored).

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("answer_cache")


def downgrade() -> None:
    # Column order, defaults and constraint names as 0017 + 0019 produced them.
    op.create_table(
        "answer_cache",
        sa.Column("question_key", sa.String, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("options", JSONB, server_default="[]", nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("hits", sa.Integer, server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("namespace", sa.String, server_default="live", nullable=False),
        sa.Column("basis", JSONB, server_default="[]", nullable=False),
        sa.Column("basis_hash", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("prompt_hash", sa.String, nullable=False),
        sa.Column("run_id", UUID(as_uuid=True)),
        sa.PrimaryKeyConstraint("namespace", "question_key", name="answer_cache_pkey"),
        sa.ForeignKeyConstraint(["run_id"], ["application_run.id"], name="answer_cache_run_id_fkey",
                                ondelete="SET NULL"),
    )
