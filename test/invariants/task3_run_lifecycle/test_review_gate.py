"""The review gate (contract §3, §4; roadmap A7): in `review` mode nothing is submitted without
approval of the current review round.

Workflow tests: real ApplicationWorkflow + worker, stub browser/LLM activities, real db_ops on the
test database. Signals are sent by name with their round: approve_submission(review_round) and
reject_submission(review_round, reason). Route tests: the real /runs routes with a FakeTemporal.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from temporalio.client import WorkflowExecutionStatus

from harness.api import FakeTemporal, api_client
from harness.contract import defect

NEW_REQUIRED_FIELD = {"key": "9", "label": "Visa type", "type": "text", "field_type": "text",
                      "required": True, "options": []}


async def approve(workflows, run_id, review_round: int) -> None:
    await (await workflows.handle(run_id)).signal("approve_submission", review_round)


async def reject(workflows, run_id, review_round: int, reason: str | None = None) -> None:
    await (await workflows.handle(run_id)).signal("reject_submission", args=[review_round, reason])


def new_field_on_refill(workflows) -> None:
    """The second FILL (the re-fill after approving round 1) finds a required field nobody approved
    and returns it as FillOutput.unmapped_new_fields (§4)."""
    fills = []

    def fill(call):
        fills.append(call)
        extra = [NEW_REQUIRED_FIELD] if len(fills) == 2 else []
        return workflows.stubs.default(call, unmapped_new_fields=extra)

    workflows.stubs.on("fill_activity", fill)


async def back_in_review(workflows, db, runtime_settings):
    """A review-mode run whose approval of round 1 sent it back to review (round 2)."""
    runtime_settings.set(submission_mode="review")
    new_field_on_refill(workflows)
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW")
    await approve(workflows, run_id, 1)
    await workflows.wait_status(run_id, "AWAITING_REVIEW", occurrence=2)
    return run_id


# ── workflow ──────────────────────────────────────────────────────────────────

@defect("contract §4", 3, "there is no review gate: a review-mode run fills and submits without approval")
async def test_review_mode_waits_for_approval_without_submitting(workflows, db, runtime_settings) -> None:
    runtime_settings.set(submission_mode="review")
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW")
    await asyncio.sleep(1.0)
    assert await workflows.workflow_status(run_id) == WorkflowExecutionStatus.RUNNING
    assert workflows.stubs.calls_to("submit_activity", run_id) == []
    row = await db.run(run_id)
    assert (row["status"], row.get("review_round")) == ("AWAITING_REVIEW", 1)
    assert row.get("review_deadline") is not None


@defect("contract §4", 3, "there is no approve signal: the run submits whether or not it is approved")
async def test_approval_refills_the_approved_answers_and_submits_exactly_once(workflows, db, runtime_settings) -> None:
    runtime_settings.set(submission_mode="review")
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW")
    await approve(workflows, run_id, 1)
    assert await workflows.wait_closed(run_id) == WorkflowExecutionStatus.COMPLETED
    submits = workflows.stubs.calls_to("submit_activity", run_id)
    assert len(submits) == 1
    assert len(workflows.stubs.calls_to("map_answers_activity", run_id)) == 1  # approved answers, no new mapping
    assert (await db.run(run_id))["status"] == "COMPLETED"
    # §7: submit is a long activity too (the testing-mode heartbeat test stops at FILL)
    assert submits[0].heartbeat_timeout and timedelta(minutes=2) <= submits[0].heartbeat_timeout <= timedelta(minutes=3)


@defect("contract §4", 3, "there is no review window: nothing expires, the run submits")
async def test_an_unapproved_run_expires_and_submits_nothing(workflows, db, runtime_settings) -> None:
    runtime_settings.set(submission_mode="review", review_window_hours=0.001)  # 3.6 s
    run_id = await workflows.enqueue()
    await workflows.wait_closed(run_id, timeout=40)
    row = await db.run(run_id)
    assert (row["status"], row.get("failure_reason")) == ("EXPIRED", "review_expired")
    assert workflows.stubs.calls_to("submit_activity", run_id) == []


@defect("contract §4", 3, "there is no reject signal: a rejected application is submitted anyway")
async def test_rejecting_a_review_cancels_the_run(workflows, db, runtime_settings) -> None:
    runtime_settings.set(submission_mode="review")
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW")
    await reject(workflows, run_id, 1, "not a fit")
    await workflows.wait_closed(run_id)
    row = await db.run(run_id)
    assert (row["status"], row.get("failure_reason")) == ("CANCELLED", "review_rejected")
    assert workflows.stubs.calls_to("submit_activity", run_id) == []


@defect("contract §4", 3, "there is no review loop: a required field that appears on re-fill is never reviewed")
async def test_a_new_required_field_on_refill_returns_the_run_to_review(workflows, db, runtime_settings) -> None:
    run_id = await back_in_review(workflows, db, runtime_settings)
    assert workflows.stubs.calls_to("submit_activity", run_id) == []
    row = await db.run(run_id)
    assert (row["status"], row.get("review_round")) == ("AWAITING_REVIEW", 2)
    await reject(workflows, run_id, 2)
    await workflows.wait_closed(run_id)


@defect("contract §4", 3, "there are no review rounds: an approval of what the user saw earlier would cover "
                          "fields they never saw")
async def test_a_stale_round_approval_after_a_return_to_review_submits_nothing(workflows, db, runtime_settings) -> None:
    run_id = await back_in_review(workflows, db, runtime_settings)
    await approve(workflows, run_id, 1)  # stale: round 1 didn't contain the new field
    await asyncio.sleep(1.5)
    assert workflows.stubs.calls_to("submit_activity", run_id) == []
    assert await workflows.workflow_status(run_id) == WorkflowExecutionStatus.RUNNING
    row = await db.run(run_id)
    assert (row["status"], row.get("review_round")) == ("AWAITING_REVIEW", 2)
    await reject(workflows, run_id, 2)
    await workflows.wait_closed(run_id)


# ── routes ────────────────────────────────────────────────────────────────────

async def _awaiting_review(db, review_round: int):
    deadline = datetime.now(timezone.utc) + timedelta(hours=48)
    return await db.seed_run(status="AWAITING_REVIEW", submission_mode="review", review_round=review_round,
                             review_deadline=deadline)


@defect("contract §4, §8", 3, "there are no approve/reject routes")
@pytest.mark.parametrize("route, body", [("approve", {}), ("reject", {"reason": "not a fit"})])
async def test_a_stale_round_is_refused_by_the_review_routes(db, route, body) -> None:
    run_id = await _awaiting_review(db, review_round=2)
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post(f"/runs/{run_id}/{route}", json={"review_round": 1, **body})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "stale_review_round"
    assert temporal.actions == []


@defect("contract §4", 3, "there are no approve/reject routes")
@pytest.mark.parametrize("route, body, signal, args", [
    ("approve", {}, "approve_submission", (2,)),
    ("reject", {"reason": "not a fit"}, "reject_submission", (2, "not a fit")),
])
async def test_the_review_routes_signal_the_current_round(db, route, body, signal, args) -> None:
    run_id = await _awaiting_review(db, review_round=2)
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post(f"/runs/{run_id}/{route}", json={"review_round": 2, **body})
    assert resp.status_code < 300, resp.text
    workflow_id = (await db.run(run_id))["temporal_workflow_id"]
    assert temporal.actions == [("signal", workflow_id, signal, args)]
    assert (await db.run(run_id))["status"] == "AWAITING_REVIEW"  # the workflow writes the result, not the route
