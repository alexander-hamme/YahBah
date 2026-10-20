"""run lifecycle: submission mode, origin, failure reason, review gate, submission claims

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-19

Contract: docs/wiki/contracts/run-lifecycle.md §9. Also repairs schema drift: job_posting.canonical_url
and .location are mapped by the ORM (and exist in the long-lived dev DB) but no earlier migration
created them, so they are added only if missing and are left alone on downgrade.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

ACTIVE = "('PENDING', 'RUNNING', 'AWAITING_REVIEW')"
# failure_reason values (yahbah.run_state.FailureReason) that pre-contract error messages start with
LEGACY_REASONS = (
    "form_not_found", "form_unmapped", "form_changed", "job_closed", "login_wall", "captcha", "blocked",
    "needs_answer", "fill_failed", "submit_button_not_found", "email_code_entry", "email_code_timeout",
    "submit_unverified",
)


def _promoted_run_fk(ondelete: str | None) -> None:
    fks = sa.inspect(op.get_bind()).get_foreign_keys("scheduled_job")
    for fk in fks:
        if fk["constrained_columns"] == ["promoted_run_id"]:
            op.drop_constraint(fk["name"], "scheduled_job", type_="foreignkey")
    op.create_foreign_key("scheduled_job_promoted_run_id_fkey", "scheduled_job", "application_run",
                          ["promoted_run_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    # Drift repair (see module docstring)
    op.execute("ALTER TABLE job_posting ADD COLUMN IF NOT EXISTS canonical_url VARCHAR")
    op.execute("ALTER TABLE job_posting ADD COLUMN IF NOT EXISTS location VARCHAR")
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_posting_canonical_url ON job_posting (canonical_url)")

    op.add_column("application_run", sa.Column("submission_mode", sa.String))  # NULL = legacy (pre-contract)
    op.add_column("application_run", sa.Column("origin", sa.String))
    op.add_column("application_run", sa.Column("failure_reason", sa.String))
    op.add_column("application_run", sa.Column("review_deadline", sa.DateTime(timezone=True)))
    op.add_column("application_run", sa.Column("review_round", sa.Integer))
    # The attempt lease (contract §7): the activity attempt that currently acts on the run.
    op.add_column("application_run", sa.Column("lease_workflow_id", sa.String))
    op.add_column("application_run", sa.Column("lease_activity_seq", sa.Integer))
    op.add_column("application_run", sa.Column("lease_attempt", sa.Integer))
    op.add_column("application_run", sa.Column(
        "retry_of_run_id", PG_UUID(as_uuid=True),
        sa.ForeignKey("application_run.id", name="application_run_retry_of_run_id_fkey", ondelete="SET NULL")))

    # Backfill. Order matters: a run that clicked submit is uncertain whatever else it says.
    op.execute("UPDATE application_run SET submission_mode = 'testing' WHERE is_test_run")
    op.execute("UPDATE application_run SET origin = CASE WHEN id IN "
               "(SELECT promoted_run_id FROM scheduled_job WHERE promoted_run_id IS NOT NULL) "
               "THEN 'discovered' ELSE 'manual' END")
    op.execute("UPDATE application_run SET status = 'SUBMIT_UNCERTAIN', failure_reason = 'submit_unverified' "
               "WHERE status = 'FAILED' AND submit_attempted_at IS NOT NULL")
    op.execute("UPDATE application_run SET status = 'CANCELLED', failure_reason = 'cancelled' "
               "WHERE status = 'FAILED' AND error_message = 'Cancelled by user'")
    op.execute("UPDATE application_run SET failure_reason = 'duplicate' WHERE status = 'DUPLICATE'")
    op.execute(sa.text(
        "UPDATE application_run SET failure_reason = split_part(error_message, ':', 1) "
        "WHERE status = 'FAILED' AND failure_reason IS NULL AND split_part(error_message, ':', 1) IN :reasons"
    ).bindparams(sa.bindparam("reasons", LEGACY_REASONS, expanding=True)))
    # At most one active run per posting (the index below): older duplicates are orphans from the
    # in-memory Temporal dev server, with no workflow behind them.
    op.execute(f"""
        UPDATE application_run r SET
            status = CASE WHEN r.submit_attempted_at IS NOT NULL THEN 'SUBMIT_UNCERTAIN' ELSE 'FAILED' END,
            failure_reason = 'superseded',
            error_message = 'superseded by a newer active run for the same posting (migration 0018)',
            completed_at = now(), updated_at = now()
        WHERE r.status IN {ACTIVE} AND EXISTS (
            SELECT 1 FROM application_run n WHERE n.job_posting_id = r.job_posting_id AND n.id <> r.id
              AND n.status IN {ACTIVE} AND (n.created_at, n.id) > (r.created_at, r.id))
    """)
    op.drop_column("application_run", "is_test_run")

    op.create_index("uq_application_run_active_posting", "application_run", ["job_posting_id"], unique=True,
                    postgresql_where=sa.text(f"status IN {ACTIVE}"))

    op.create_table(
        "submission_claim",
        sa.Column("posting_key", sa.String, primary_key=True),  # batch 1: str(job_posting_id)
        sa.Column("run_id", PG_UUID(as_uuid=True),
                  sa.ForeignKey("application_run.id", name="submission_claim_run_id_fkey", ondelete="SET NULL")),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Every run that ever clicked submit holds its posting's claim, so deleting it keeps the protection.
    op.execute("INSERT INTO submission_claim (posting_key, run_id, claimed_at) "
               "SELECT DISTINCT ON (job_posting_id) job_posting_id::text, id, submit_attempted_at "
               "FROM application_run WHERE submit_attempted_at IS NOT NULL "
               "ORDER BY job_posting_id, submit_attempted_at")

    _promoted_run_fk(ondelete="SET NULL")


def downgrade() -> None:
    _promoted_run_fk(ondelete=None)
    op.drop_table("submission_claim")
    op.drop_index("uq_application_run_active_posting", table_name="application_run")

    op.add_column("application_run", sa.Column("is_test_run", sa.Boolean, server_default="false", nullable=False))
    op.execute("UPDATE application_run SET is_test_run = true WHERE submission_mode = 'testing'")
    # Statuses the pre-contract code doesn't know become FAILED (with the reason in error_message).
    op.execute("UPDATE application_run SET error_message = 'Cancelled by user' "
               "WHERE status = 'CANCELLED' AND failure_reason = 'cancelled'")
    op.execute("UPDATE application_run SET status = 'FAILED', "
               "error_message = coalesce(error_message, failure_reason, status) "
               "WHERE status IN ('SUBMIT_UNCERTAIN', 'CANCELLED', 'EXPIRED', 'AWAITING_REVIEW')")

    op.drop_column("application_run", "retry_of_run_id")
    op.drop_column("application_run", "lease_attempt")
    op.drop_column("application_run", "lease_activity_seq")
    op.drop_column("application_run", "lease_workflow_id")
    op.drop_column("application_run", "review_round")
    op.drop_column("application_run", "review_deadline")
    op.drop_column("application_run", "failure_reason")
    op.drop_column("application_run", "origin")
    op.drop_column("application_run", "submission_mode")
