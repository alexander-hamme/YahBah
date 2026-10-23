"""The run-level `headed` flag (contract §2, "Headed fallback"): a run that discover moved to the
headed fallback browser stays headed for every later browser stage, across review waits; the
`browser_headed_fallback` setting is snapshotted at run creation; a retry starts headless.

Workflow tests: the real workflow and worker, stub browser/LLM activities (as in
test_workflow_lifecycle.py). Compatibility tests: old-shape payloads through Temporal's own data
converter, and a history recorded without the new fields replayed by the current workflow.
"""
import base64
import json

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowHistory
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError

from harness.api import api_client

NEW_REQUIRED_FIELD = {"key": "9", "label": "Visa type", "type": "text", "field_type": "text",
                      "required": True, "options": []}
BROWSER_INPUTS = ("map_form_activity", "fill_activity", "submit_activity")


async def approve(workflows, run_id, review_round: int) -> None:
    await (await workflows.handle(run_id)).signal("approve_submission", review_round)


def discover_headed(workflows) -> None:
    workflows.stubs.on("discover_activity", lambda call: workflows.stubs.default(call, headed=True))


def new_field_on_first_refill(workflows) -> None:
    """The re-fill after round 1's approval finds a required field nobody approved (back to review)."""
    fills = []

    def fill(call):
        fills.append(call)
        return workflows.stubs.default(call, unmapped_new_fields=[NEW_REQUIRED_FIELD] if len(fills) == 2 else [])

    workflows.stubs.on("fill_activity", fill)


def browser_calls(workflows, run_id) -> list:
    return [c for c in workflows.stubs.calls if c.run_id == str(run_id) and c.name in BROWSER_INPUTS]


# ── the flag through the workflow ─────────────────────────────────────────────

async def test_a_headed_discover_keeps_every_later_browser_stage_headed_across_reviews(
        workflows, db, runtime_settings) -> None:
    runtime_settings.set(submission_mode="review")
    discover_headed(workflows)
    new_field_on_first_refill(workflows)
    run_id = await workflows.enqueue()

    await workflows.wait_status(run_id, "AWAITING_REVIEW")
    await approve(workflows, run_id, 1)
    await workflows.wait_status(run_id, "AWAITING_REVIEW", occurrence=2)  # the new field's round
    await approve(workflows, run_id, 2)
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED

    calls = browser_calls(workflows, run_id)
    assert [c.name for c in calls] == ["map_form_activity", "fill_activity", "fill_activity", "fill_activity",
                                       "submit_activity"]
    assert [c.input.get("refill") for c in calls if c.name == "fill_activity"] == [False, True, True]
    assert [c.input.get("headed") for c in calls] == [True] * 5, "a browser stage after a headed discover was headless"
    [mapped] = workflows.stubs.calls_to("map_new_fields_activity", run_id)
    assert "headed" not in mapped.input  # no browser: answer mapping only


async def test_a_headless_discover_keeps_every_browser_stage_headless(workflows, db, runtime_settings) -> None:
    runtime_settings.set(submission_mode="review")
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW")
    await approve(workflows, run_id, 1)
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED
    calls = browser_calls(workflows, run_id)
    assert [c.name for c in calls] == ["map_form_activity", "fill_activity", "fill_activity", "submit_activity"]
    assert [c.input.get("headed") for c in calls] == [False] * 4


async def test_a_retried_run_starts_headless(workflows, db, runtime_settings) -> None:
    """The retry is a new run (§7): its discover opens headless and nothing of the old run's flag carries over."""
    runtime_settings.set(submission_mode="review")
    discover_headed(workflows)

    def map_form(call):
        if call.input.get("headed"):
            raise ApplicationError("form_not_found: no form", type="form_not_found", non_retryable=True)
        return workflows.stubs.default(call)

    workflows.stubs.on("map_form_activity", map_form)
    original = await workflows.enqueue()
    assert await workflows.wait_closed(original, timeout=60) == WorkflowExecutionStatus.FAILED
    assert (await db.run(original))["status"] == "FAILED"

    workflows.stubs.on("discover_activity", workflows.stubs.default)  # this time the page isn't walled
    async with api_client(workflows.client) as client:
        resp = await client.post(f"/runs/{original}/retry")
    assert resp.status_code == 200, resp.text
    retry = resp.json()["run_id"]
    await workflows.wait_status(retry, "AWAITING_REVIEW", timeout=60)

    [discover] = workflows.stubs.calls_to("discover_activity", retry)
    assert "headed" not in discover.input, "discover has no headed input: it always opens headless first"
    assert [c.input.get("headed") for c in browser_calls(workflows, retry)] == [False, False]


# ── the setting's snapshot ────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, snapshot", [(None, True), (True, True), (False, False), ("yes", True)])
async def test_the_fallback_setting_is_snapshotted_into_discover(
        workflows, db, runtime_settings, stored, snapshot) -> None:
    runtime_settings.set(submission_mode="testing")
    if stored is None:
        runtime_settings.unset("browser_headed_fallback")
    else:
        runtime_settings.set(browser_headed_fallback=stored)
    run_id = await workflows.enqueue()
    runtime_settings.set(browser_headed_fallback=not snapshot)  # affects new runs only
    assert await workflows.wait_closed(run_id, timeout=60) == WorkflowExecutionStatus.COMPLETED
    [discover] = workflows.stubs.calls_to("discover_activity", run_id)
    assert discover.input["headed_fallback"] is snapshot


# ── compatibility with runs started before the flag ───────────────────────────

def _roundtrip(raw: dict, cls):
    """`raw` encoded and decoded the way Temporal passes an activity's input or result."""
    converter = DataConverter.default.payload_converter
    [value] = converter.from_payloads(converter.to_payloads([raw]), [cls])
    return value


def test_old_shape_payloads_decode_headless_and_without_fallback() -> None:
    from yahbah.schemas import DiscoverInput, DiscoverOutput, FillInput, MapFormInput, SubmitInput
    from yahbah.workflows.application import ApplicationWorkflowInput

    url = "https://jobs.harness.example/acme/1"
    assert _roundtrip({"run_id": "r", "job_url": url, "submission_mode": "review", "origin": "manual",
                       "review_window_hours": 48.0}, ApplicationWorkflowInput).browser_headed_fallback is False
    assert _roundtrip({"run_id": "r", "job_url": url}, DiscoverInput).headed_fallback is False
    assert _roundtrip({"page_url": url, "board": "generic", "account_email": "a@example.test",
                       "job_description": ""}, DiscoverOutput).headed is False
    assert _roundtrip({"run_id": "r", "job_url": url}, MapFormInput).headed is False
    assert _roundtrip({"run_id": "r", "job_url": url, "form_schema_dict": {}, "field_mappings": [],
                       "job_description": "", "cover_letter_path": None}, FillInput).headed is False
    assert _roundtrip({"run_id": "r", "job_url": url, "form_schema_dict": {}, "field_mappings": [], "written": {},
                       "job_description": "", "cover_letter_path": None, "cover_letter_text": None,
                       "account_email": "a@example.test"}, SubmitInput).headed is False


_NEW_FIELDS = {"headed", "headed_fallback", "browser_headed_fallback"}


def _strip_new_fields(node, stripped: list):
    """A history as the workflow before the flag recorded it: no headed fields in any JSON payload."""
    if isinstance(node, dict):
        if node.get("metadata", {}).get("encoding") == base64.b64encode(b"json/plain").decode() and "data" in node:
            value = json.loads(base64.b64decode(node["data"]))
            if isinstance(value, dict) and _NEW_FIELDS & value.keys():
                stripped.extend(sorted(_NEW_FIELDS & value.keys()))
                value = {k: v for k, v in value.items() if k not in _NEW_FIELDS}
                node = {**node, "data": base64.b64encode(json.dumps(value).encode()).decode()}
            return node
        return {k: _strip_new_fields(v, stripped) for k, v in node.items()}
    if isinstance(node, list):
        return [_strip_new_fields(v, stripped) for v in node]
    return node


async def test_a_run_waiting_in_review_from_before_the_flag_replays_on_the_current_workflow(
        workflows, db, runtime_settings) -> None:
    """A run in AWAITING_REVIEW when this change deploys: its history has no headed fields anywhere.
    Only activity inputs and results changed (no command was added, removed or reordered), so it
    replays without a nondeterminism error (its later inputs decode headless: the converter test)."""
    from temporalio.worker import Replayer

    from yahbah.workflows.application import ApplicationWorkflow

    runtime_settings.set(submission_mode="review")
    run_id = await workflows.enqueue()
    await workflows.wait_status(run_id, "AWAITING_REVIEW")
    handle = await workflows.handle(run_id)
    recorded = await handle.fetch_history()
    stripped: list[str] = []
    old = WorkflowHistory.from_json(handle.id, _strip_new_fields(json.loads(recorded.to_json()), stripped))
    # The workflow input, discover's input and result, map_form's and the first fill's inputs.
    assert sorted(set(stripped)) == sorted(_NEW_FIELDS) and len(stripped) == 5, stripped

    result = await Replayer(workflows=[ApplicationWorkflow]).replay_workflow(old, raise_on_replay_failure=False)
    assert result.replay_failure is None, result.replay_failure
