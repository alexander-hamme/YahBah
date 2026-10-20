"""submission_mode fails closed and is snapshotted per run (contract §3, §4). A run created the way
POST /jobs creates one (yahbah.jobs.submit_job_for_application), with a FakeTemporal."""
import pytest

from harness.api import FakeTemporal
from harness.contract import call_with_supported, defect


async def _create_run(db):
    from yahbah.jobs import submit_job_for_application

    temporal = FakeTemporal()
    run_id = await call_with_supported(submit_job_for_application, "https://jobs.harness.example/acme/mode-1",
                                       temporal, origin="manual")
    return await db.run(run_id), temporal.started[0]["arg"]


@defect("contract §3", 3, "runs snapshot no submission_mode, and a missing or invalid setting doesn't mean review")
@pytest.mark.parametrize("setting", [None, "yolo", "auto_reviewed"], ids=["missing", "invalid", "batch2_value"])
async def test_a_missing_or_invalid_submission_mode_creates_review_runs(db, runtime_settings, setting) -> None:
    if setting is None:
        runtime_settings.unset("submission_mode")
    else:
        runtime_settings.set(submission_mode=setting)
    row, workflow_input = await _create_run(db)
    assert row.get("submission_mode") == "review"
    assert getattr(workflow_input, "submission_mode", None) == "review"


@defect("contract §3, §4", 3, "the run's mode and review window aren't snapshotted into the workflow input")
async def test_mode_origin_and_review_window_are_snapshotted_at_creation(db, runtime_settings) -> None:
    runtime_settings.set(submission_mode="testing", review_window_hours=1.5)
    row, workflow_input = await _create_run(db)
    runtime_settings.set(submission_mode="review", review_window_hours=48)  # later changes affect new runs only
    assert (row.get("submission_mode"), row.get("origin")) == ("testing", "manual")
    assert (getattr(workflow_input, "submission_mode", None), getattr(workflow_input, "review_window_hours", None)) == (
        "testing", 1.5)
    assert (await db.run(row["id"])).get("submission_mode") == "testing"
