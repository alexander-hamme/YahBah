"""Self-tests of the disposable Postgres fixture."""
import uuid

import pytest

from harness.db import PREFIX, SchemaNotReady


async def test_database_is_disposable_migrated_and_patched_in(db) -> None:
    assert db.database.name.startswith(PREFIX)
    head = await db.fetchone("SELECT version_num FROM alembic_version")
    assert head and head["version_num"]

    import yahbah.db.session as session_module
    import yahbah.workflows.activities.db_ops as db_ops
    assert db_ops.AsyncSessionLocal is db.Session
    assert session_module.AsyncSessionLocal is db.Session

    # A real activity writes through the patched session
    run_id = await db.seed_run(status="RUNNING")
    await db_ops.mark_run_completed_activity(str(run_id))
    assert (await db.run(run_id))["status"] == "COMPLETED"


async def test_status_log_and_peak_running(db) -> None:
    a, b = await db.seed_run(status="PENDING"), await db.seed_run(status="PENDING")
    for run_id, status in ((a, "RUNNING"), (b, "RUNNING"), (a, "COMPLETED"), (b, "FAILED")):
        await db.execute("UPDATE application_run SET status = :s WHERE id = :id", s=status, id=run_id)
    assert await db.statuses_seen(a) == ["PENDING", "RUNNING", "COMPLETED"]
    assert await db.peak_running() == 2


async def test_tables_start_empty(db) -> None:
    assert await db.runs() == []
    assert await db.status_log() == []


async def test_seeding_a_column_the_schema_lacks_fails_loudly(db) -> None:
    with pytest.raises(SchemaNotReady, match="no_such_column"):
        await db.seed_run(no_such_column=1)


async def test_testing_mode_is_seeded_in_whichever_form_the_schema_has(db) -> None:
    run_id = await db.seed_run(status="COMPLETED", submission_mode="testing")
    row = await db.run(run_id)
    assert row.get("submission_mode") == "testing" or row.get("is_test_run") is True


async def test_session_lookups_are_by_uuid(db) -> None:
    with pytest.raises(AssertionError):
        await db.run(uuid.uuid4())
