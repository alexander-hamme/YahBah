"""Batch-1 review, task 3 (run lifecycle): exit paths where "may have been submitted" can still end as
"not applied", and evidence that can go unregistered (contract §1, §2, §5, §6).

Found by QA's close-out review of 2026-10-20 as strict xfails; all fixed the same day, now regression tests.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError

from harness.activities import JOB_URL, fill_input, running_run, submit_input
from harness.api import FakeTemporal, api_client

NOW = datetime.now(timezone.utc)


class DatabaseDown(OSError):
    """What a dropped Postgres connection looks like to the activity (not an ApplicationError)."""


# ── the agent tripwire must survive a failure to record it ───────────────────

async def test_a_tripwire_whose_recording_fails_is_never_retried(db, activity_env, browser_activities,
                                                                registry, monkeypatch) -> None:
    """activities/browser.py `_stage_failed`: the possible submission is recorded first and the
    non-retryable possibly_submitted error raised after. If the record raises, the error that leaves the
    activity is the DB error, which is retryable."""
    from yahbah.agent.discover import StageFailed

    _, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), JOB_URL)

    async def tripped(*args, **kwargs):
        raise StageFailed("possibly_submitted", "an agent action left the form")

    async def database_down(*args, **kwargs):
        raise DatabaseDown("server closed the connection unexpectedly")

    monkeypatch.setattr(browser_activities, "fill_form", tripped)
    monkeypatch.setattr(browser_activities, "record_possible_submission", database_down)
    with pytest.raises(ApplicationError) as exc:
        await env.call(browser_activities.fill_activity, fill_input(run_id))
    assert (exc.value.type, exc.value.non_retryable) == ("possibly_submitted", True)


async def test_a_possibly_submitted_failure_ends_submit_uncertain_even_unrecorded(workflows, db,
                                                                                 runtime_settings) -> None:
    """The terminal write is the last place that can still hold the rule "possibly_submitted always ends
    SUBMIT_UNCERTAIN" (§2). Here the fill activity reports possibly_submitted without the attempt having
    been recorded (its record_possible_submission failed, as above)."""
    from yahbah.run_state import possibly_applied

    runtime_settings.set(submission_mode="review")

    def tripped(call):
        raise ApplicationError("possibly_submitted: an agent action left the form", type="possibly_submitted",
                               non_retryable=True)

    workflows.stubs.on("fill_activity", tripped)
    run_id = await workflows.enqueue()
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.FAILED
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"]) == ("SUBMIT_UNCERTAIN", "possibly_submitted")
    async with db.session() as session:
        assert await possibly_applied(session, row["job_posting_id"]) is not None


async def test_possibly_submitted_recorded_by_the_activity_ends_submit_uncertain(workflows, db,
                                                                                 runtime_settings) -> None:
    """Guard (holds today): with the attempt recorded, the same failure ends SUBMIT_UNCERTAIN."""
    from yahbah.run_state import record_possible_submission

    runtime_settings.set(submission_mode="review")

    async def tripped(call):
        row = await db.run(call.run_id)
        await record_possible_submission(call.run_id, call.workflow_id, row["job_posting_id"])
        raise ApplicationError("possibly_submitted: an agent action left the form", type="possibly_submitted",
                               non_retryable=True)

    workflows.stubs.on("fill_activity", tripped)
    run_id = await workflows.enqueue()
    await workflows.wait_closed(run_id, timeout=60)
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"]) == ("SUBMIT_UNCERTAIN", "possibly_submitted")
    assert "FAILED" not in await db.statuses_seen(run_id)


# ── submit evidence must be findable ──────────────────────────────────────────

async def test_submit_evidence_is_registered_even_if_its_first_registration_fails(
        db, activity_env, browser_activities, registry, monkeypatch) -> None:
    posting, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), JOB_URL)
    real_persist = browser_activities.persist_artifact_activity
    failures = []

    async def flaky_persist(run_id, artifact_type, path, metadata=None):
        if artifact_type == "submit_evidence" and not failures:
            failures.append(path)
            raise DatabaseDown("server closed the connection unexpectedly")
        return await real_persist(run_id, artifact_type, path, metadata)

    async def submit_and_verify(session, board, ctx, *, claim, record, **kwargs):
        assert await claim()
        for rnd in (1, 2):
            try:
                await record({"schema": 1, "kind": "round", "round": rnd, "verdict": "pending"})
            except Exception:
                pass  # submit_and_verify's _Recorder logs and ignores a failing record
        return SimpleNamespace(confirmation_url="u", confirmation_text="Thanks", confirmation_html="")

    monkeypatch.setattr(browser_activities, "persist_artifact_activity", flaky_persist)
    monkeypatch.setattr(browser_activities, "submit_and_verify", submit_and_verify)
    await env.call(browser_activities.submit_activity, submit_input(run_id))

    assert failures, "the first registration was not attempted"
    rows = await db.fetchall("SELECT path FROM application_artifact WHERE artifact_type = 'submit_evidence'")
    assert len(rows) == 1, "the submit evidence is on disk but not registered as an artifact of the run"
    assert [json.loads(line)["round"] for line in Path(rows[0]["path"]).read_text().splitlines()] == [1, 2]


# ── POST /jobs and the canonical URL ──────────────────────────────────────────

async def test_post_jobs_refuses_a_url_that_was_applied_to_under_another_posting(db, runtime_settings,
                                                                                 monkeypatch) -> None:
    import yahbah.api.routes.jobs as jobs_route

    async def unchanged(url: str) -> str:
        return url

    monkeypatch.setattr(jobs_route, "normalize_job_url", unchanged)  # redirect-following fetch: offline here
    direct = "https://jobs.harness.example/acme/canonical-7"
    via_aggregator = await db.seed_posting("https://jobs.harness.example/aggregator/acme-7", canonical_url=direct)
    applied = await db.seed_run(via_aggregator, status="COMPLETED", submission_mode="review",
                                submit_attempted_at=NOW, completed_at=NOW)
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post("/jobs", json={"job_url": direct})
    assert resp.status_code == 409, resp.text
    assert (resp.json()["detail"]["code"], resp.json()["detail"].get("run_id")) == ("already_applied", str(applied))
    assert temporal.started == []
