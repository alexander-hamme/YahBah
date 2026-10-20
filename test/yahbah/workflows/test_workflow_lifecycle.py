"""ApplicationWorkflow end to end on a local Temporal server (contract §1, §4, §6, §7).

The real workflow and yahbah.worker.main(); browser/LLM activities are stubs under their real names,
db_ops activities are real, on the disposable test database. The invariant suite
(test/invariants/task3_run_lifecycle/) covers the contract's headline cases; these cover the
remaining exit paths: failures after the claim, cancellation, duplicates, superseded runs,
an attempt that overruns its timeout, and run slots with reviews in between.

Waits are on events and conditions, never on how long something takes; the one test that needs a
real timeout to expire is marked `slow`. (No time-skipping server is available offline.)
"""
import asyncio
from datetime import timedelta
import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.common import RetryPolicy
from temporalio.worker import UnsandboxedWorkflowRunner


async def approve(workflows, run_id, review_round: int = 1) -> None:
    await (await workflows.handle(run_id)).signal("approve_submission", review_round)


async def claim(db, call) -> None:
    """What submit_and_verify does right before its click: the real submission claim."""
    from yahbah.run_state import claim_submission

    row = await db.run(call.run_id)
    assert await claim_submission(call.run_id, call.workflow_id, row["job_posting_id"])


def holding(entered: asyncio.Event | None = None):
    """A stub that sets `entered`, then heartbeats (so a cancellation reaches it) until cancelled."""
    async def stub(call):
        if entered is not None:
            entered.set()
        while True:
            activity.heartbeat()
            await asyncio.sleep(0.05)
    return stub


@pytest.fixture
async def fast_heartbeats(workflows, monkeypatch):
    """Heartbeats (and so cancellations) reach the server within half a second."""
    import yahbah.worker as worker_module

    monkeypatch.setattr(worker_module, "MAX_HEARTBEAT_THROTTLE", timedelta(seconds=0.5))
    await workflows.worker.restart()
    return workflows


@pytest.fixture
async def patchable_timeouts(workflows, monkeypatch):
    """The workflow runs unsandboxed, so patches of yahbah.workflows.application's timeouts apply.
    (The sandbox re-imports the workflow module from source for each run.)"""
    import yahbah.worker as worker_module

    make = worker_module.Worker  # the harness's worker factory
    monkeypatch.setattr(worker_module, "Worker",
                        lambda client, **kw: make(client, workflow_runner=UnsandboxedWorkflowRunner(), **kw))
    monkeypatch.setattr(worker_module, "MAX_HEARTBEAT_THROTTLE", timedelta(seconds=0.5))
    await workflows.worker.restart()
    return workflows


# ── submission outcomes ───────────────────────────────────────────────────────

async def test_a_failure_after_the_claim_ends_submit_uncertain_and_blocks_the_job(workflows, db, runtime_settings):
    from yahbah.run_state import possibly_applied

    runtime_settings.set(submission_mode="review")

    async def submit(call):
        await claim(db, call)
        raise ApplicationError("submit_unverified: no confirmation after 4 rounds", type="submit_unverified",
                               non_retryable=True)

    workflows.stubs.on("submit_activity", submit)
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW", timeout=60)
    await approve(workflows, run_id)
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.FAILED

    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"]) == ("SUBMIT_UNCERTAIN", "submit_unverified")
    assert "FAILED" not in await db.statuses_seen(run_id)
    assert len(workflows.stubs.calls_to("submit_activity", run_id)) == 1  # never retried
    async with db.session() as session:
        assert (await possibly_applied(session, row["job_posting_id"])).run_id == run_id


async def test_a_failure_before_the_claim_ends_failed_with_its_reason(workflows, db, runtime_settings):
    runtime_settings.set(submission_mode="review")

    def submit(call):
        raise ApplicationError("submit_button_not_found: no submit button", type="submit_button_not_found",
                               non_retryable=True)

    workflows.stubs.on("submit_activity", submit)
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW", timeout=60)
    await approve(workflows, run_id)
    await workflows.wait_closed(run_id, timeout=60)
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"], row["submit_attempted_at"]) == (
        "FAILED", "submit_button_not_found", None)
    assert workflows.stubs.calls_to("close_session_activity", run_id), "the browser was not closed"


async def test_a_completed_run_records_its_snapshot(workflows, db, runtime_settings):
    runtime_settings.set(submission_mode="review", review_window_hours=2)
    baseline = {"7": "site-prefilled"}
    workflows.stubs.on("fill_activity", lambda call: workflows.stubs.default(
        call, baseline=baseline if call.input.get("refill") else None))
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW", timeout=60)
    runtime_settings.set(submission_mode="testing")  # affects new runs only
    await approve(workflows, run_id)
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED
    row = await db.run(run_id)
    assert (row["status"], row["submission_mode"], row["origin"], row["review_round"]) == (
        "COMPLETED", "review", "manual", 1)
    assert await db.statuses_seen(run_id) == ["PENDING", "RUNNING", "AWAITING_REVIEW", "RUNNING", "COMPLETED"]

    [refill] = [c for c in workflows.stubs.calls_to("fill_activity", run_id) if c.input.get("refill")]
    assert refill.input["review_round"] == 2
    [submit] = workflows.stubs.calls_to("submit_activity", run_id)
    assert submit.input["mapped_form_dict"]["fields"], "submit can't re-fill a lost browser without the mapped form"
    assert submit.input["baseline"] == baseline, "submit must check the page against the approved re-fill's baseline"


async def test_testing_mode_fills_once_and_submits_nothing(workflows, db, runtime_settings):
    runtime_settings.set(submission_mode="testing")
    run_id = await workflows.enqueue()
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED
    row = await db.run(run_id)
    assert (row["status"], row["submission_mode"], row["review_round"]) == ("COMPLETED", "testing", None)
    assert workflows.stubs.calls_to("submit_activity", run_id) == []
    [fill] = workflows.stubs.calls_to("fill_activity", run_id)
    assert fill.input["refill"] is False
    # The form asks for no cover letter, so none is written; answers are scoped to the job's location.
    assert workflows.stubs.calls_to("generate_cover_letter_activity", run_id) == []
    assert fill.input["cover_letter_path"] is None
    [answers] = workflows.stubs.calls_to("map_answers_activity", run_id)
    assert answers.input["job_location"] == fill.input["job_location"] == "Boston, MA"


async def test_a_cover_letter_is_written_only_when_the_form_asks_for_one(workflows, db, runtime_settings):
    letter = {"key": "2", "label": "Cover letter", "field_type": "file", "source": "cover_letter", "value": "",
              "confidence": 1.0}
    workflows.stubs.on("map_answers_activity", lambda call: workflows.stubs.default(
        call, field_mappings=[{**letter, "key": "1"}]))
    workflows.stubs.on("generate_cover_letter_activity", lambda call: workflows.stubs.default(
        call, cover_letter_path="/tmp/cover.pdf", cover_letter_text="Dear Acme"))
    run_id = await workflows.enqueue()
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED
    assert len(workflows.stubs.calls_to("generate_cover_letter_activity", run_id)) == 1
    [fill] = workflows.stubs.calls_to("fill_activity", run_id)
    assert (fill.input["cover_letter_path"], fill.input["cover_letter_text"]) == ("/tmp/cover.pdf", "Dear Acme")


async def test_a_duplicate_found_after_discovery_ends_duplicate_and_the_workflow_completes(workflows, db):
    """The job's canonical URL is only known once the page is open (here an aggregator link); the run
    that turns out to be a duplicate of an uncertain one ends DUPLICATE."""
    from datetime import datetime, timezone

    canonical = "https://jobs.harness.example/acme/dup-1"
    other = await db.seed_posting("https://jobs.harness.example/acme/dup-1-direct", canonical_url=canonical)
    await db.seed_run(other, status="SUBMIT_UNCERTAIN", submit_attempted_at=datetime.now(timezone.utc))
    workflows.stubs.on("discover_activity", lambda call: workflows.stubs.default(call, canonical_url=canonical))
    run_id = await workflows.enqueue("https://jobs.harness.example/aggregator/dup-1")
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"]) == ("DUPLICATE", "duplicate")
    assert workflows.stubs.calls_to("fill_activity", run_id) == []


# ── cancellation ──────────────────────────────────────────────────────────────

async def test_cancel_during_a_stage_stops_it_closes_the_browser_and_ends_cancelled(fast_heartbeats, db):
    workflows = fast_heartbeats
    entered = asyncio.Event()
    workflows.stubs.on("fill_activity", holding(entered))
    run_id = await workflows.enqueue()
    await asyncio.wait_for(entered.wait(), 30)
    await (await workflows.handle(run_id)).cancel()

    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.CANCELED
    [fill] = workflows.stubs.calls_to("fill_activity", run_id)
    assert fill.cancelled and not fill.completed
    assert workflows.stubs.completed("close_session_activity", run_id), "the browser was not closed"
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"]) == ("CANCELLED", "cancelled")


async def test_cancel_after_the_claim_ends_submit_uncertain(fast_heartbeats, db, runtime_settings):
    workflows = fast_heartbeats
    runtime_settings.set(submission_mode="review")
    claimed = asyncio.Event()
    hold = holding(claimed)

    async def submit(call):
        await claim(db, call)
        await hold(call)

    workflows.stubs.on("submit_activity", submit)
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW", timeout=60)
    await approve(workflows, run_id)
    await asyncio.wait_for(claimed.wait(), 30)
    await (await workflows.handle(run_id)).cancel()

    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.CANCELED
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"]) == ("SUBMIT_UNCERTAIN", "cancelled")


async def test_a_cancel_that_trips_the_wire_ends_submit_uncertain(fast_heartbeats, db):
    """A cancellation that lands after an agent left the form surfaces as possibly_submitted, not as a
    cancellation (agent.llm.run_agent): the run ends SUBMIT_UNCERTAIN, whoever recorded what."""
    workflows = fast_heartbeats
    entered = asyncio.Event()
    hold = holding(entered)

    async def fill(call):
        try:
            await hold(call)
        except asyncio.CancelledError:
            raise ApplicationError("possibly_submitted: an agent action left the form", type="possibly_submitted",
                                   non_retryable=True) from None

    workflows.stubs.on("fill_activity", fill)
    run_id = await workflows.enqueue()
    await asyncio.wait_for(entered.wait(), 30)
    await (await workflows.handle(run_id)).cancel()
    await workflows.wait_closed(run_id, timeout=60)
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"]) == ("SUBMIT_UNCERTAIN", "possibly_submitted")
    assert row["submit_attempted_at"] is not None
    assert await db.fetchall("SELECT run_id FROM submission_claim") == [{"run_id": run_id}]


async def test_cancel_during_review_ends_cancelled(workflows, db, runtime_settings):
    runtime_settings.set(submission_mode="review")
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW", timeout=60)
    await (await workflows.handle(run_id)).cancel()
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.CANCELED
    assert (await db.run(run_id))["status"] == "CANCELLED"
    assert workflows.stubs.calls_to("submit_activity", run_id) == []


# ── ownership ─────────────────────────────────────────────────────────────────

async def test_a_superseded_workflow_stops_without_writing(workflows, db):
    entered, release = asyncio.Event(), asyncio.Event()

    async def map_form(call):
        entered.set()
        await asyncio.wait_for(release.wait(), 60)
        return workflows.stubs.default(call)

    workflows.stubs.on("map_form_activity", map_form)
    run_id = await workflows.enqueue()
    await asyncio.wait_for(entered.wait(), 30)
    handle = await workflows.handle(run_id)
    # Another workflow now owns the row (what a retry or reconciliation leaves behind).
    await db.execute("UPDATE application_run SET temporal_workflow_id = 'application-someone-else' WHERE id = :id",
                     id=run_id)
    release.set()
    with pytest.raises(Exception):
        await asyncio.wait_for(handle.result(), 60)
    assert (await handle.describe()).status == WorkflowExecutionStatus.FAILED
    row = await db.run(run_id)
    assert (row["status"], row["current_state"], row["failure_reason"]) == ("RUNNING", "MAP_FORM", None)
    assert workflows.stubs.calls_to("map_answers_activity", run_id) == []


@pytest.mark.slow
async def test_an_attempt_that_overruns_its_timeout_is_cancelled_before_it_can_act(
        patchable_timeouts, db, monkeypatch):
    """A start-to-close overrun cancels the old attempt in the worker (it heartbeats, so it learns
    of the timeout) while the retry carries the run on (audit probe C). Real time: the 2 s timeout
    has to expire."""
    from yahbah.workflows import application
    from yahbah.workflows.activities import heartbeat

    workflows = patchable_timeouts
    monkeypatch.setattr(application, "AGENTIC", timedelta(seconds=2))
    monkeypatch.setattr(application, "RETRY", RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=0.2)))
    monkeypatch.setattr(heartbeat, "HEARTBEAT_EVERY", 0.05)
    never = asyncio.Event()

    async def discover(call):
        if call.attempt == 1:
            await heartbeat.heartbeating(asyncio.wait_for)(never.wait(), 60)  # overruns, heartbeating
        return workflows.stubs.default(call)

    workflows.stubs.on("discover_activity", discover)
    run_id = await workflows.enqueue()
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED
    first, *retries = workflows.stubs.calls_to("discover_activity", run_id)
    assert first.cancelled and not first.completed, "the timed-out attempt kept running beside its retry"
    assert retries and retries[-1].completed


# ── run slots ─────────────────────────────────────────────────────────────────

async def test_runs_waiting_for_review_hold_no_run_slot(workflows, db, runtime_settings, monkeypatch):
    from yahbah.config import settings

    monkeypatch.setattr(settings, "max_concurrent_runs", 1)
    runtime_settings.set(submission_mode="review")
    first, second = await workflows.enqueue(), await workflows.enqueue()
    for run_id in (first, second):
        await workflows.wait_status(run_id, "AWAITING_REVIEW", timeout=60)
    await approve(workflows, first)
    await approve(workflows, second)
    for run_id in (first, second):
        assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED
    assert await db.peak_running() == 1
