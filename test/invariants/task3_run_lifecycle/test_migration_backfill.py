"""Migration 0018's backfill (contract §9): rows written before the contract are seeded on a
database migrated only to 0017, then `alembic upgrade head` runs over them."""
import uuid
from datetime import datetime, timezone

import pytest

from harness import db as dbh
from harness.contract import defect

pytestmark = pytest.mark.db  # (its own database, not the `db` fixture's)

PRE_CONTRACT = "0017"
NOW = datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def backfilled():
    """{case: run id} after seeding at 0017 and upgrading to head."""
    try:
        database = dbh.create_database_at(PRE_CONTRACT)
    except dbh.PostgresUnavailable as exc:
        pytest.skip(f"Postgres unreachable ({exc})")
    try:
        runs = {}

        def seed(case, status, **values):
            posting = uuid.uuid4()
            dbh.execute(database, "INSERT INTO job_posting (id, url, ats_type, created_at) "
                                  "VALUES (%(id)s, %(url)s, 'generic', %(now)s)",
                        {"id": posting, "url": f"https://jobs.harness.example/acme/{case}", "now": NOW})
            run_id = uuid.uuid4()
            cols = {"id": run_id, "job_posting_id": posting, "job_url": f"https://jobs.harness.example/acme/{case}",
                    "status": status, "created_at": NOW, "updated_at": NOW, **values}
            dbh.execute(database, f"INSERT INTO application_run ({', '.join(cols)}) "
                                  f"VALUES ({', '.join(f'%({c})s' for c in cols)})", cols)
            runs[case] = run_id

        seed("failed_after_submit_attempt", "FAILED", submit_attempted_at=NOW, error_message="submit_unverified")
        seed("cancelled_by_user", "FAILED", error_message="Cancelled by user")
        seed("failed_before_submit", "FAILED", error_message="form_not_found")
        seed("test_run", "COMPLETED", is_test_run=True)
        seed("real_run", "COMPLETED", is_test_run=False)
        seed("promoted", "FAILED", error_message="form_not_found")
        dbh.execute(database, "INSERT INTO scheduled_job (job_url, source, gmail_message_id, promote_after, status, "
                              "promoted_run_id) VALUES ('u', 'other', 'm', %(now)s, 'PROMOTED', %(run)s)",
                    {"now": NOW, "run": runs["promoted"]})
        dbh.migrate(database, "head")
        yield {case: dbh.execute(database, "SELECT * FROM application_run WHERE id = %(id)s", {"id": run_id})[0]
               for case, run_id in runs.items()}
    finally:
        dbh.drop_database(database)


@defect("contract §9", 3, "no migration 0018: a FAILED run that clicked submit stays FAILED (looks never applied)")
def test_a_failed_run_with_a_submit_attempt_becomes_submit_uncertain(backfilled) -> None:
    assert backfilled["failed_after_submit_attempt"]["status"] == "SUBMIT_UNCERTAIN"


@defect("contract §9", 3, "no migration 0018: user cancellations are stored as FAILED")
def test_a_user_cancellation_becomes_cancelled(backfilled) -> None:
    row = backfilled["cancelled_by_user"]
    assert (row["status"], row.get("failure_reason")) == ("CANCELLED", "cancelled")


def test_an_ordinary_failure_stays_failed(backfilled) -> None:
    assert backfilled["failed_before_submit"]["status"] == "FAILED"


@defect("contract §3, §9", 3, "no migration 0018: test runs are marked by is_test_run, not submission_mode")
def test_is_test_run_becomes_testing_mode(backfilled) -> None:
    assert backfilled["test_run"].get("submission_mode") == "testing"
    assert backfilled["real_run"].get("submission_mode") is None  # legacy
    assert "is_test_run" not in backfilled["test_run"]


@defect("contract §3, §9", 3, "no migration 0018: runs have no origin")
def test_origin_is_discovered_for_promoted_runs_else_manual(backfilled) -> None:
    assert backfilled["promoted"].get("origin") == "discovered"
    assert backfilled["real_run"].get("origin") == "manual"
