"""Retry and delete through the real /runs routes (contract §7, §9; audit Temporal F1/F2, Forms F2.3, Product F4)."""
import uuid
from datetime import datetime, timezone

from temporalio.client import WorkflowExecutionStatus

from harness.api import FakeTemporal, api_client
from harness.contract import defect

NOW = datetime.now(timezone.utc)


@defect("contract §7", 3, "retry accepts a run whose submit was attempted, and the retried run can click again")
async def test_retry_of_an_attempted_run_is_refused(db) -> None:
    run_id = await db.seed_run(status="FAILED", submit_attempted_at=NOW, error_message="submit_unverified")
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post(f"/runs/{run_id}/retry")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "action_not_allowed"  # §8: routes enforce allowed_actions()
    assert temporal.started == []
    assert (await db.run(run_id))["status"] == "FAILED"


@defect("contract §7", 3, "retry resets the original row in place instead of creating a linked run")
async def test_retry_creates_a_new_run_linked_to_the_original(db, runtime_settings) -> None:
    runtime_settings.set(submission_mode="review")
    original = await db.seed_run(status="FAILED", error_message="form_not_found", completed_at=NOW,
                                 account_email="jane.doe+job-ab12@example.test")
    before = await db.run(original)
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post(f"/runs/{original}/retry")
    assert resp.status_code == 200, resp.text

    new_id = uuid.UUID(resp.json()["run_id"])
    assert new_id != original, "the retry reused the original run"
    new = await db.run(new_id)
    assert new.get("retry_of_run_id") == original
    assert (new["status"], new["job_posting_id"], new.get("submission_mode")) == ("PENDING", before["job_posting_id"], "review")
    after = await db.run(original)
    for column in ("status", "error_message", "account_email", "temporal_workflow_id", "completed_at"):
        assert after[column] == before[column], f"retry changed the original run's {column}"
    assert [s["id"] for s in temporal.started] == [new["temporal_workflow_id"]]


@defect("contract §7", 3, "retry starts a second workflow for the run while the first is still alive")
async def test_retry_terminates_the_original_workflow_if_it_is_still_running(workflows, db) -> None:
    gate = workflows.stubs.gate("fill_activity", timeout=20)
    run_id = await workflows.enqueue()
    await gate.wait_entered()
    original_workflow = (await db.run(run_id))["temporal_workflow_id"]
    # The divergence a legacy restart sweep leaves behind: the row says FAILED, the workflow lives on.
    await db.execute("UPDATE application_run SET status = 'FAILED', error_message = 'Worker restarted' "
                     "WHERE id = :id", id=run_id)

    async with api_client(workflows.client) as client:
        resp = await client.post(f"/runs/{run_id}/retry")
    assert resp.status_code == 200, resp.text
    described = await workflows.client.get_workflow_handle(original_workflow).describe()
    assert described.status == WorkflowExecutionStatus.TERMINATED, (
        f"the original workflow is still {described.status.name} beside the retry")


@defect("contract §9", 3, "scheduled_job.promoted_run_id has no ON DELETE SET NULL: a promoted run can't be deleted")
async def test_deleting_a_promoted_run_succeeds(db) -> None:
    run_id = await db.seed_run(status="FAILED", completed_at=NOW)
    job = await db.insert("scheduled_job", job_url=(await db.run(run_id))["job_url"], source="other",
                          gmail_message_id="harness-message-1", promote_after=NOW, status="PROMOTED",
                          promoted_run_id=run_id)
    async with api_client(FakeTemporal()) as client:
        resp = await client.delete(f"/runs/{run_id}")
    assert resp.status_code == 200, resp.text
    assert await db.fetchone("SELECT id FROM application_run WHERE id = :id", id=run_id) is None
    row = await db.fetchone("SELECT promoted_run_id FROM scheduled_job WHERE id = :id", id=job["id"])
    assert row["promoted_run_id"] is None
