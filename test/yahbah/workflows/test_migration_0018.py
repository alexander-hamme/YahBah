"""Migration 0018 (contract §9) up, down and up again on its own disposable database, seeded with
pre-contract rows at 0017. The backfill rules the contract names are in
test/invariants/task3_run_lifecycle/test_migration_backfill.py; these cover the rest of the
migration: orphaned active runs, legacy failure reasons, claims, constraints and the downgrade."""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from harness import db as dbh
from harness.env import REPO

pytestmark = pytest.mark.db

NOW = datetime.now(timezone.utc)


def alembic(database, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": dbh._checked(database.url).render_as_string(hide_password=False)}
    proc = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, f"alembic {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"


def columns(database, table: str) -> set[str]:
    return {r["column_name"] for r in dbh.execute(
        database, "SELECT column_name FROM information_schema.columns WHERE table_name = %(t)s", {"t": table})}


@pytest.fixture(scope="module")
def database():
    try:
        database = dbh.create_database_at("0017")
    except dbh.PostgresUnavailable as exc:
        pytest.skip(f"Postgres unreachable ({exc})")
    yield database
    dbh.drop_database(database)


@pytest.fixture(scope="module")
def seeded(database):
    def posting(name):
        posting_id = uuid.uuid4()
        dbh.execute(database, "INSERT INTO job_posting (id, url, ats_type, created_at) "
                              "VALUES (%(id)s, %(url)s, 'generic', %(now)s)",
                    {"id": posting_id, "url": f"https://jobs.harness.example/acme/{name}", "now": NOW})
        return posting_id

    def run(posting_id, status, created=NOW, **values):
        run_id = uuid.uuid4()
        cols = {"id": run_id, "job_posting_id": posting_id, "job_url": "https://jobs.harness.example/acme/x",
                "status": status, "created_at": created, "updated_at": created, **values}
        dbh.execute(database, f"INSERT INTO application_run ({', '.join(cols)}) "
                              f"VALUES ({', '.join(f'%({c})s' for c in cols)})", cols)
        return run_id

    orphans = posting("orphans")
    runs = {
        "older_orphan": run(orphans, "PENDING", NOW - timedelta(days=90)),
        "newer_orphan": run(orphans, "PENDING", NOW - timedelta(days=1)),
        "attempted": run(posting("attempted"), "FAILED", submit_attempted_at=NOW, error_message="submit_unverified: x"),
        "cancelled": run(posting("cancelled"), "FAILED", error_message="Cancelled by user"),
        "legacy_reason": run(posting("legacy"), "FAILED", error_message="form_not_found: no form on the page"),
        "test_run": run(posting("test"), "COMPLETED", is_test_run=True),
        "promoted": run(posting("promoted"), "FAILED", error_message="job_closed: gone"),
    }
    dbh.execute(database, "INSERT INTO scheduled_job (job_url, source, gmail_message_id, promote_after, status, "
                          "promoted_run_id) VALUES ('u', 'other', 'm', %(now)s, 'PROMOTED', %(run)s)",
                {"now": NOW, "run": runs["promoted"]})
    dbh.migrate(database, "head")
    return runs


def row(database, run_id) -> dict:
    return dbh.execute(database, "SELECT * FROM application_run WHERE id = %(id)s", {"id": run_id})[0]


def test_older_active_runs_of_a_posting_are_closed_so_the_unique_index_can_exist(database, seeded) -> None:
    older, newer = row(database, seeded["older_orphan"]), row(database, seeded["newer_orphan"])
    assert (older["status"], older["failure_reason"]) == ("FAILED", "superseded")
    assert newer["status"] == "PENDING"
    with pytest.raises(psycopg.errors.UniqueViolation):
        dbh.execute(database, "UPDATE application_run SET status = 'RUNNING' WHERE id = %(id)s",
                    {"id": seeded["older_orphan"]})


def test_legacy_failure_reasons_come_from_the_error_message_prefix(database, seeded) -> None:
    assert row(database, seeded["legacy_reason"])["failure_reason"] == "form_not_found"
    assert row(database, seeded["attempted"])["failure_reason"] == "submit_unverified"


def test_every_attempted_run_holds_its_postings_claim(database, seeded) -> None:
    attempted = row(database, seeded["attempted"])
    assert dbh.execute(database, "SELECT posting_key, run_id FROM submission_claim") == [
        {"posting_key": str(attempted["job_posting_id"]), "run_id": seeded["attempted"]}]


def test_deleting_runs_keeps_claims_and_unlinks_promotions(database, seeded) -> None:
    for name in ("attempted", "promoted"):
        dbh.execute(database, "DELETE FROM application_run WHERE id = %(id)s", {"id": seeded[name]})
    assert dbh.execute(database, "SELECT run_id FROM submission_claim") == [{"run_id": None}]
    assert dbh.execute(database, "SELECT promoted_run_id FROM scheduled_job") == [{"promoted_run_id": None}]


def test_downgrade_restores_the_pre_contract_schema_and_upgrade_runs_again(database, seeded) -> None:
    alembic(database, "downgrade", "0017")
    run_cols = columns(database, "application_run")
    assert "is_test_run" in run_cols
    assert not {"submission_mode", "origin", "failure_reason", "review_round", "review_deadline",
                "retry_of_run_id"} & run_cols
    assert {"canonical_url", "location"} <= columns(database, "job_posting")  # drift repair stays
    assert columns(database, "submission_claim") == set()
    assert row(database, seeded["test_run"])["is_test_run"] is True
    cancelled = row(database, seeded["cancelled"])
    assert (cancelled["status"], cancelled["error_message"]) == ("FAILED", "Cancelled by user")
    statuses = {r["status"] for r in dbh.execute(database, "SELECT status FROM application_run")}
    assert statuses <= {"PENDING", "RUNNING", "COMPLETED", "FAILED", "DUPLICATE"}

    alembic(database, "upgrade", "head")
    assert row(database, seeded["test_run"])["submission_mode"] == "testing"
    assert row(database, seeded["cancelled"])["status"] == "CANCELLED"
