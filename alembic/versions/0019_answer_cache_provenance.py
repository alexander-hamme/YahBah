"""answer_cache provenance: namespace, basis, basis_hash, model, prompt_hash, run_id

The primary key becomes (namespace, question_key), so debug runs (namespace 'debug') never share
rows with live runs. Rows written before this migration have no provenance, so the mapper could
never trust them; they are deleted (one of them is an unbacked "Available from" answer, audit
2026-10-18 LLM F2). The cache only ever holds regenerable answers.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A row without provenance is never trusted (its basis, model and prompt are unknown), so the
    # new columns have no backfill: the pre-0019 rows go. Downgrade restores the schema, not them.
    op.execute("DELETE FROM answer_cache")
    op.drop_constraint("answer_cache_pkey", "answer_cache", type_="primary")
    op.add_column("answer_cache", sa.Column("namespace", sa.String, server_default="live", nullable=False))
    op.add_column("answer_cache", sa.Column("basis", JSONB, server_default="[]", nullable=False))
    op.add_column("answer_cache", sa.Column("basis_hash", sa.String, nullable=False))
    op.add_column("answer_cache", sa.Column("model", sa.String, nullable=False))
    op.add_column("answer_cache", sa.Column("prompt_hash", sa.String, nullable=False))
    op.add_column("answer_cache", sa.Column(
        "run_id", UUID(as_uuid=True), sa.ForeignKey("application_run.id", ondelete="SET NULL")))
    op.create_primary_key("answer_cache_pkey", "answer_cache", ["namespace", "question_key"])


def downgrade() -> None:
    # The old primary key is question_key alone: debug rows would collide with live ones.
    op.execute("DELETE FROM answer_cache WHERE namespace <> 'live'")
    op.drop_constraint("answer_cache_pkey", "answer_cache", type_="primary")
    for column in ("run_id", "prompt_hash", "model", "basis_hash", "basis", "namespace"):
        op.drop_column("answer_cache", column)
    op.create_primary_key("answer_cache_pkey", "answer_cache", ["question_key"])
