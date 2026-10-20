"""Run ownership across worker restarts and overlapping attempts (contract §1, §7; audit Temporal F1).

The real ApplicationWorkflow and yahbah.worker.main() run on a local Temporal server with stub
browser/LLM activities; db_ops activities are real, on the disposable test database.
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError

from harness.contract import defect
from harness.forms import CappedAsyncio


@defect("contract §1, §7", 3, "a worker restart marks the in-flight run FAILED and frees its run slot (probe B)")
async def test_a_worker_restart_mid_fill_never_shows_failed_and_completes_once(workflows, db, monkeypatch) -> None:
    from yahbah.config import settings

    monkeypatch.setattr(settings, "max_concurrent_runs", 1)
    gate = workflows.stubs.gate("fill_activity", attempts={1}, timeout=3)
    first = await workflows.enqueue()
    second = await workflows.enqueue()  # queued behind `first` for the run slot
    held = await gate.wait_entered()
    assert held.run_id == str(first)

    await workflows.worker.restart()  # graceful stop during FILL, then a fresh worker
    gate.release.set()
    for run_id in (first, second):
        assert await workflows.wait_closed(run_id, timeout=45) == WorkflowExecutionStatus.COMPLETED

    problems = []
    for run_id in (first, second):
        seen = await db.statuses_seen(run_id)
        if "FAILED" in seen:
            problems.append(f"run {run_id} was shown FAILED: {seen}")
        if seen.count("COMPLETED") != 1 or seen[-1] != "COMPLETED":
            problems.append(f"run {run_id} did not complete exactly once: {seen}")
        if len(workflows.stubs.completed("fill_activity", run_id)) != 1:
            problems.append(f"run {run_id} filled {len(workflows.stubs.completed('fill_activity', run_id))} times")
    if (peak := await db.peak_running()) > settings.max_concurrent_runs:
        problems.append(f"peak RUNNING {peak} > max_concurrent_runs {settings.max_concurrent_runs}")
    assert not problems, "\n".join(problems)


@defect("contract §1, §7", 3, "the worker marks RUNNING runs FAILED when it starts and when it stops")
async def test_the_worker_writes_no_status_at_start_or_stop(workflows, db) -> None:
    running = await db.seed_run(status="RUNNING", current_state="FILL")  # its workflow is resumed by Temporal
    pending = await db.seed_run(status="PENDING")
    await workflows.worker.restart()
    assert await db.statuses_seen(running) == ["RUNNING"]
    assert await db.statuses_seen(pending) == ["PENDING"]


def _write(activity_name: str):
    from yahbah.workflows.activities import db_ops

    fn = getattr(db_ops, activity_name)
    return (fn, ("FILL",)) if activity_name == "update_run_state_activity" else (fn, ())


@defect("contract §7", 3, "status writes are not fenced by the owning workflow id or a terminal status")
@pytest.mark.parametrize("activity_name", ["update_run_state_activity", "mark_run_completed_activity"])
@pytest.mark.parametrize("case", ["stale_workflow", "terminal_status"])
async def test_a_superseded_workflows_writes_are_rejected(db, activity_env, activity_name, case) -> None:
    status, owner = ("RUNNING", "application-retry") if case == "stale_workflow" else ("FAILED", "application-old")
    run_id = await db.seed_run(status=status, current_state="MAP_FORM", temporal_workflow_id=owner)
    fn, extra = _write(activity_name)
    with pytest.raises(ApplicationError) as exc:
        await activity_env(workflow_id="application-old").call(fn, str(run_id), *extra)
    assert exc.value.type == "superseded" and exc.value.non_retryable
    row = await db.run(run_id)
    assert (row["status"], row["current_state"]) == (status, "MAP_FORM")


@defect("contract §7", 3, "long activities have no heartbeat_timeout: a dead worker is noticed after 45 min, "
                          "and a timed-out attempt keeps running beside its retry (probe C)")
async def test_long_activities_heartbeat_within_two_to_three_minutes(workflows) -> None:
    run_id = await workflows.enqueue()  # testing mode: every stage up to FILL
    await workflows.wait_closed(run_id)
    long = [c for c in workflows.stubs.calls
            if c.start_to_close_timeout and c.start_to_close_timeout > timedelta(minutes=10)]
    assert long, "no long-running activity was called"
    bad = {c.name: c.heartbeat_timeout for c in long
           if c.heartbeat_timeout is None or not timedelta(minutes=2) <= c.heartbeat_timeout <= timedelta(minutes=3)}
    assert not bad, f"long activities without a 2-3 min heartbeat_timeout: {bad}"


class _FakeBrowserSession:
    """Stands in for browser-use's BrowserSession inside SessionRegistry (no Chrome)."""

    def __init__(self, browser_profile=None, **kwargs) -> None:
        self.browser_profile = browser_profile or SimpleNamespace(user_data_dir=None)

    async def start(self) -> None:
        pass

    async def navigate_to(self, url: str) -> None:
        self.url = url

    async def kill(self) -> None:
        pass


@defect("contract §7", 3, "SessionRegistry gives a run's live browser to any caller, so an attempt "
                          "superseded by its retry keeps driving the page")
async def test_a_superseded_attempt_cannot_get_the_runs_browser(activity_env, monkeypatch) -> None:
    from yahbah.agent import session as session_module

    monkeypatch.setattr(session_module, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(session_module, "asyncio", CappedAsyncio(0))  # open()'s fixed 3 s render wait
    registry = session_module.SessionRegistry()
    run_id, workflow_id, url = "run-1", "application-run-1", "https://jobs.harness.example/acme/1"
    attempt1 = activity_env(workflow_id=workflow_id, attempt=1)
    attempt2 = activity_env(workflow_id=workflow_id, attempt=2)

    opened = await attempt1.call(registry.open, run_id, url)
    assert await attempt2.call(registry.get, run_id) is opened  # a higher attempt takes the session over
    assert await attempt1.call(registry.get, run_id) is None, (
        "attempt 1 (timed out and retried) still gets the run's live browser")
    assert await activity_env(workflow_id="application-other", attempt=3).call(registry.get, run_id) is None, (
        "another workflow gets the run's live browser")
