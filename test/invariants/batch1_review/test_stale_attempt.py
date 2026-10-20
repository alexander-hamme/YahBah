"""Batch-1 review, task 3: an attempt that overran its start-to-close timeout can't act once its retry
has taken over (contract §7: fenced writes, browser ownership; roadmap task 3 "timeout overlap").

lifecycle's test_workflow_lifecycle.py::test_an_attempt_that_overruns_its_timeout_is_cancelled_before_it_can_act
shows the SDK cancels a timed-out attempt that heartbeats and honours the cancellation. That proves
nothing about an attempt that keeps running: a library call that absorbs CancelledError, or a worker
cut off from the server (its retry starts on reconnect, and the old attempt only learns it was
superseded at its next heartbeat round trip). Here the old submit attempt swallows the cancellation,
waits until the retry has taken the run's browser, and then tries to act.

  browser      SessionRegistry.get() gives it nothing                       holds today
  claim        claim_submission() must refuse it (it would click next)      holds (attempt lease)
  state write  a fenced write must refuse it                                 holds (attempt lease)

Real time: the 2 s timeout has to expire (marked slow).
"""
import asyncio
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner

from harness.activities import JOB_URL

pytestmark = pytest.mark.slow


@pytest.fixture
async def overrunning(workflows, registry, monkeypatch):
    """The workflow unsandboxed (so the patched timeouts apply), a 2 s start-to-close for long
    activities, fast retries, and heartbeats (cancellations) reaching the server within 0.5 s."""
    import yahbah.worker as worker_module
    from yahbah.workflows import application

    make = worker_module.Worker  # the harness's worker factory
    monkeypatch.setattr(worker_module, "Worker",
                        lambda client, **kw: make(client, workflow_runner=UnsandboxedWorkflowRunner(), **kw))
    monkeypatch.setattr(worker_module, "MAX_HEARTBEAT_THROTTLE", timedelta(seconds=0.5))
    monkeypatch.setattr(application, "AGENTIC", timedelta(seconds=2))
    monkeypatch.setattr(application, "RETRY", RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=0.2)))
    await workflows.worker.restart()
    return workflows


async def stale_submit_attempt(workflows, db, registry, runtime_settings) -> dict:
    """Run a review-mode job to SUBMIT whose first submit attempt overruns and ignores its cancellation;
    returns what that stale attempt could do once its retry owned the browser."""
    from yahbah.run_state import claim_submission
    from yahbah.workflows.activities.db_ops import fenced_update

    runtime_settings.set(submission_mode="review")
    taken, stale_done = asyncio.Event(), asyncio.Event()
    seen: dict = {}

    async def submit(call):
        run_id = call.run_id
        if call.attempt == 1:
            await registry.open(run_id, JOB_URL)  # this attempt's browser
            try:
                while True:  # overruns its timeout, heartbeating
                    activity.heartbeat()
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                seen["cancelled"] = True  # absorbed, like a library call that swallows it
            await asyncio.wait_for(taken.wait(), 30)
            seen["browser"] = registry.get(run_id)
            try:
                await fenced_update(run_id, current_state="SUBMIT")
                seen["state_write"] = "accepted"
            except ApplicationError as exc:
                seen["state_write"] = exc.type
            row = await db.run(run_id)
            seen["claim"] = await claim_submission(run_id, call.workflow_id, row["job_posting_id"])
            stale_done.set()
            return workflows.stubs.default(call)
        seen["retry_has_browser"] = registry.get(run_id) is not None
        taken.set()
        await asyncio.wait_for(stale_done.wait(), 30)
        return workflows.stubs.default(call)

    workflows.stubs.on("submit_activity", submit)
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW", timeout=60)
    await (await workflows.handle(run_id)).signal("approve_submission", 1)
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED
    assert seen.get("cancelled"), "the overrunning attempt was never cancelled"
    assert seen.get("retry_has_browser"), "the retry did not get the run's browser"
    return seen


@pytest.mark.parametrize("what, expected", [
    pytest.param("browser", None, id="browser"),
    pytest.param("claim", False, id="claim"),
    pytest.param("state_write", "superseded", id="state_write"),
])
async def test_a_stale_attempt_cannot_act_after_its_retry_took_over(overrunning, db, registry, runtime_settings,
                                                                    what, expected) -> None:
    seen = await stale_submit_attempt(overrunning, db, registry, runtime_settings)
    assert seen[what] == expected
