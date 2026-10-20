"""Activities of the run lifecycle, one at a time in an activity context on the test database
(contract §4, §6, §7): the submission claim and the agent tripwire at the browser boundary, review
re-fills, fenced terminal writes, run slots, background heartbeats and browser ownership.

The in-browser code (submit_and_verify, fill_form) is replaced by fakes that behave as its
interface promises; its own behaviour is tested in test/yahbah/agent/ and test/invariants/task4_*.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.exceptions import ApplicationError

from harness.forms import CappedAsyncio

NOW = datetime.now(timezone.utc)
FIELD = {"key": "1", "label": "First name", "field_type": "text", "required": True, "options": []}
MAPPING = {"key": "1", "label": "First name", "field_type": "text", "source": "first_name", "value": "Jane",
           "confidence": 1.0}
NEW_REQUIRED = {"key": "9", "label": "Visa type", "type": "text", "required": True, "options": []}


class FakeBrowserSession:
    """browser-use's BrowserSession inside SessionRegistry, without Chrome."""

    def __init__(self, browser_profile=None, **kwargs) -> None:
        self.browser_profile = browser_profile or SimpleNamespace(user_data_dir=None)
        self.killed = False

    async def start(self) -> None:
        pass

    async def navigate_to(self, url: str) -> None:
        self.url = url

    async def get_current_page_url(self) -> str:
        return self.url

    async def take_screenshot(self, path: str, full_page: bool = True) -> None:
        Path(path).write_bytes(b"png")

    async def kill(self) -> None:
        self.killed = True


@pytest.fixture
def registry(monkeypatch):
    from yahbah.agent import session as session_module

    monkeypatch.setattr(session_module, "BrowserSession", FakeBrowserSession)
    monkeypatch.setattr(session_module, "asyncio", CappedAsyncio(0))  # open()'s fixed render wait
    fresh = session_module.SessionRegistry()
    monkeypatch.setattr(session_module.SessionRegistry, "_instance", fresh)
    return fresh


@pytest.fixture
def browser_activities(monkeypatch, registry):
    """yahbah.workflows.activities.browser with the page-level code faked out."""
    from yahbah.workflows.activities import browser

    async def profile():
        return SimpleNamespace(resume_path="/tmp/resume.pdf", email="jane@example.test")

    async def no_discover(session, trace):
        return None

    async def form_schema(session):
        return [{"key": "1", "label": "First name"}]

    async def read_values(session, keys):
        return {k: {"value": "Jane"} for k in keys}

    monkeypatch.setattr(browser, "_load_profile", profile)
    monkeypatch.setattr(browser, "discover", no_discover)
    monkeypatch.setattr(browser, "get_form_schema", form_schema)
    monkeypatch.setattr(browser, "read_values", read_values)
    monkeypatch.setattr(browser, "submitted_values", lambda ctx, values: [{"key": k} for k in ctx.fields])
    return browser


def fill_input(run_id, **kw):
    from yahbah.schemas import FillInput

    return FillInput(run_id=str(run_id), job_url="https://jobs.harness.example/acme/1",
                     form_schema_dict={"fields": [FIELD], "page_url": "", "board": "generic"},
                     field_mappings=[MAPPING], job_description="", cover_letter_path=None, **kw)


def submit_input(run_id):
    from yahbah.schemas import SubmitInput

    return SubmitInput(run_id=str(run_id), job_url="https://jobs.harness.example/acme/1",
                       form_schema_dict={"fields": [FIELD], "page_url": "", "board": "generic"},
                       field_mappings=[MAPPING], written={"1": "Jane"}, job_description="",
                       cover_letter_path=None, cover_letter_text=None, account_email="jane+1@example.test")


async def running_run(db):
    posting = await db.seed_posting()
    return posting, await db.seed_run(posting, status="RUNNING", current_state="SUBMIT")


async def claims(db) -> list[dict]:
    return await db.fetchall("SELECT posting_key, run_id FROM submission_claim")


# ── submit: the claim and the per-round evidence ─────────────────────────────

async def test_submit_claims_before_clicking_and_appends_each_rounds_evidence(
        db, activity_env, browser_activities, registry, monkeypatch) -> None:
    posting, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), "https://jobs.harness.example/acme/1")
    clicked = []

    async def submit_and_verify(session, board, ctx, *, email, already_attempted, claim, record, map_new_fields, beat):
        assert map_new_fields is None and not already_attempted
        assert ctx.baseline == {"7": "site-prefilled"}, "the approved re-fill's baseline didn't reach submit"
        assert (await db.run(run_id))["submit_attempted_at"] is None
        assert await claim() is True
        clicked.append((await db.run(run_id))["submit_attempted_at"])
        await record({"round": 1, "verdict": "pending"})
        await record({"round": 2, "verdict": "submitted"})
        return SimpleNamespace(confirmation_url="u", confirmation_text="Thanks", confirmation_html="")

    monkeypatch.setattr(browser_activities, "submit_and_verify", submit_and_verify)
    submit = submit_input(run_id)
    submit.baseline = {"7": "site-prefilled"}
    out = await env.call(browser_activities.submit_activity, submit)

    assert out.confirmation_text == "Thanks"
    assert clicked and clicked[0] is not None, "the click went out before submit_attempted_at was written"
    assert await claims(db) == [{"posting_key": str(posting), "run_id": run_id}]
    [artifact] = await db.fetchall("SELECT path FROM application_artifact WHERE artifact_type = 'submit_evidence'")
    lines = Path(artifact["path"]).read_text().splitlines()
    assert [json.loads(line)["round"] for line in lines] == [1, 2]
    assert registry.get(str(run_id)) is None  # closed after a verified submit


async def test_submit_with_the_job_claimed_by_another_run_never_clicks(
        db, activity_env, browser_activities, registry, monkeypatch) -> None:
    from yahbah.agent.discover import StageFailed

    posting, run_id = await running_run(db)
    other = await db.seed_run(posting, status="SUBMIT_UNCERTAIN", submit_attempted_at=NOW)
    await db.insert("submission_claim", posting_key=str(posting), run_id=other, claimed_at=NOW)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), "https://jobs.harness.example/acme/1")

    async def submit_and_verify(session, board, ctx, *, claim, **kw):
        if not await claim():
            raise StageFailed("claim_denied", "the submission claim was not granted")
        raise AssertionError("clicked without a claim")

    monkeypatch.setattr(browser_activities, "submit_and_verify", submit_and_verify)
    with pytest.raises(ApplicationError) as exc:
        await env.call(browser_activities.submit_activity, submit_input(run_id))
    assert (exc.value.type, exc.value.non_retryable) == ("claim_denied", True)
    assert (await db.run(run_id))["submit_attempted_at"] is None  # rolled back: this run ends FAILED
    assert await claims(db) == [{"posting_key": str(posting), "run_id": other}]


async def test_submit_after_a_lost_browser_and_an_earlier_claim_never_clicks_again(
        db, activity_env, browser_activities, monkeypatch) -> None:
    posting = await db.seed_posting()
    run_id = await db.seed_run(posting, status="RUNNING", submit_attempted_at=NOW)

    async def submit_and_verify(*args, **kwargs):
        raise AssertionError("submit_and_verify called without the browser that clicked")

    monkeypatch.setattr(browser_activities, "submit_and_verify", submit_and_verify)
    with pytest.raises(ApplicationError) as exc:
        await activity_env(workflow_id=f"application-{run_id}", attempt=2).call(
            browser_activities.submit_activity, submit_input(run_id))
    assert (exc.value.type, exc.value.non_retryable) == ("submit_unverified", True)


@pytest.mark.parametrize("stage", ["fill", "submit"])
async def test_the_agent_tripwire_records_a_possible_submission(
        db, activity_env, browser_activities, registry, monkeypatch, stage) -> None:
    """StageFailed("possibly_submitted"): no claim was taken, but the run is marked attempted and the
    job claimed before the error, so the run ends SUBMIT_UNCERTAIN (contract §6 addendum)."""
    from yahbah.agent.discover import StageFailed
    from yahbah.run_state import possibly_applied

    posting, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), "https://jobs.harness.example/acme/1")

    async def tripped(*args, **kwargs):
        raise StageFailed("possibly_submitted", "an agent action left the form")

    if stage == "fill":
        monkeypatch.setattr(browser_activities, "fill_form", tripped)
        call = env.call(browser_activities.fill_activity, fill_input(run_id))
    else:
        monkeypatch.setattr(browser_activities, "submit_and_verify", tripped)
        call = env.call(browser_activities.submit_activity, submit_input(run_id))
    with pytest.raises(ApplicationError) as exc:
        await call
    assert (exc.value.type, exc.value.non_retryable) == ("possibly_submitted", True)
    assert (await db.run(run_id))["submit_attempted_at"] is not None
    assert await claims(db) == [{"posting_key": str(posting), "run_id": run_id}]
    async with db.session() as session:
        assert (await possibly_applied(session, posting)).run_id == run_id


# ── fill: evidence, review re-fills ───────────────────────────────────────────

async def test_a_first_fill_saves_round_evidence_and_closes_the_browser(
        db, activity_env, browser_activities, registry, monkeypatch) -> None:
    _, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), "https://jobs.harness.example/acme/1")
    mappers = []

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        mappers.append(map_new_fields)
        return []

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    out = await env.call(browser_activities.fill_activity, fill_input(run_id))

    assert mappers[0] is not None, "a first fill maps fields that appear while filling"
    assert out.unmapped_new_fields == []
    rows = await db.fetchall("SELECT artifact_type, artifact_metadata FROM application_artifact ORDER BY created_at")
    assert [(r["artifact_type"], r["artifact_metadata"].get("review_round")) for r in rows] == [
        ("submitted_values", 1), ("screenshot", 1)]
    assert registry.get(str(run_id)) is None  # closed for review


async def test_a_refill_with_a_new_required_field_returns_it_and_closes_the_browser(
        db, activity_env, browser_activities, registry, monkeypatch) -> None:
    _, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        assert map_new_fields is None, "a re-fill must not map anything new"
        ctx.unmapped_new_fields = [NEW_REQUIRED, {**NEW_REQUIRED, "key": "10", "required": False}]
        return ['[9] "Visa type" is required but empty']  # not a failure: it goes back to review

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    out = await env.call(browser_activities.fill_activity, fill_input(run_id, review_round=2, refill=True))
    assert [f["key"] for f in out.unmapped_new_fields] == ["9", "10"]
    assert registry.get(str(run_id)) is None
    [row] = await db.fetchall("SELECT artifact_metadata FROM application_artifact WHERE artifact_type = 'submitted_values'")
    assert row["artifact_metadata"] == {"review_round": 2}


async def test_a_clean_refill_keeps_the_browser_open_for_submit(
        db, activity_env, browser_activities, registry, monkeypatch) -> None:
    _, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        ctx.written = {"1": "Jane"}
        ctx.baseline = {"7": ""}  # what fill_form records in locked mode
        return []

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    out = await env.call(browser_activities.fill_activity, fill_input(run_id, review_round=2, refill=True))
    assert (out.written, out.baseline) == ({"1": "Jane"}, {"7": ""})
    assert await env.call(registry.get, str(run_id)) is not None


async def test_a_refilled_form_that_changed_since_review_fails_form_changed(
        db, activity_env, browser_activities, monkeypatch) -> None:
    _, run_id = await running_run(db)

    async def form_schema(session):
        return [{"key": "1", "label": "Legal first name"}]

    monkeypatch.setattr(browser_activities, "get_form_schema", form_schema)
    with pytest.raises(ApplicationError) as exc:
        await activity_env(workflow_id=f"application-{run_id}").call(
            browser_activities.fill_activity, fill_input(run_id, review_round=2, refill=True))
    assert (exc.value.type, exc.value.non_retryable) == ("form_changed", True)


# ── fenced terminal writes and run slots ──────────────────────────────────────

@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "EXPIRED", "DUPLICATE"])
async def test_an_attempted_run_ends_submit_uncertain_whatever_ends_it(db, activity_env, status) -> None:
    from yahbah.workflows.activities.db_ops import finish_run_activity

    run_id = await db.seed_run(status="RUNNING", submit_attempted_at=NOW)
    await activity_env(workflow_id=f"application-{run_id}").call(
        finish_run_activity, str(run_id), status, "timeout", "boom")
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"], row["error_message"]) == ("SUBMIT_UNCERTAIN", "timeout", "boom")
    assert row["completed_at"] is not None


async def test_entering_review_frees_the_slot_and_an_approved_run_queues_for_one(
        db, activity_env, monkeypatch) -> None:
    from yahbah.config import settings
    from yahbah.workflows.activities import db_ops

    monkeypatch.setattr(settings, "max_concurrent_runs", 1)
    monkeypatch.setattr(db_ops, "asyncio", CappedAsyncio(0.05))
    first, second = await db.seed_run(status="RUNNING"), await db.seed_run(status="PENDING")
    first_env = activity_env(workflow_id=f"application-{first}")
    await first_env.call(db_ops.enter_review_activity, str(first), 1, NOW.isoformat())
    await activity_env(workflow_id=f"application-{second}").call(db_ops.acquire_run_slot_activity, str(second))
    assert (await db.run(second))["status"] == "RUNNING"

    approved = asyncio.create_task(first_env.call(db_ops.acquire_run_slot_activity, str(first)))
    await asyncio.sleep(0.3)
    assert (await db.run(first))["status"] == "PENDING" and not approved.done()  # queued behind `second`
    await db.execute("UPDATE application_run SET status = 'COMPLETED' WHERE id = :id", id=second)
    await asyncio.wait_for(approved, 5)
    assert await db.statuses_seen(first) == ["RUNNING", "AWAITING_REVIEW", "PENDING", "RUNNING"]
    assert await db.peak_running() == 1


# ── heartbeats ────────────────────────────────────────────────────────────────

async def test_a_long_activity_heartbeats_while_one_call_blocks(activity_env, monkeypatch) -> None:
    from yahbah.workflows.activities import heartbeat

    monkeypatch.setattr(heartbeat, "HEARTBEAT_EVERY", 0.05)
    beats = []
    env = activity_env()
    env.on_heartbeat = lambda *details: beats.append(details)

    @heartbeat.heartbeating
    async def slow_model_call(seconds: float) -> str:
        await asyncio.sleep(seconds)
        return "done"

    assert await env.call(slow_model_call, 0.4) == "done"
    assert len(beats) >= 4


def test_heartbeating_keeps_the_activity_signature() -> None:
    import typing

    from yahbah.schemas import FillInput, FillOutput
    from yahbah.workflows.activities.browser import fill_activity

    assert typing.get_type_hints(fill_activity) == {"input": FillInput, "return": FillOutput}


# ── browser ownership across activities ───────────────────────────────────────

async def test_a_later_activity_takes_the_browser_and_an_earlier_one_loses_it(activity_env, registry) -> None:
    from yahbah.agent.session import SessionSuperseded

    run, wf, url = "run-1", "application-run-1", "https://jobs.harness.example/acme/1"
    discover = activity_env(workflow_id=wf, activity_id="3", attempt=2)
    fill = activity_env(workflow_id=wf, activity_id="11", attempt=1)
    opened = await discover.call(registry.open, run, url)
    assert await fill.call(registry.get, run) is opened
    assert await discover.call(registry.get, run) is None
    with pytest.raises(SessionSuperseded):
        await discover.call(registry.open, run, url)
    await discover.call(registry.close, run)  # an older attempt can't close it either
    assert await fill.call(registry.get, run) is opened
    await fill.call(registry.close, run)
    assert opened.killed and registry.get(run) is None


async def test_a_superseded_attempt_gets_no_browser_from_a_stage(db, activity_env, browser_activities,
                                                                 registry) -> None:
    _, run_id = await running_run(db)
    wf = f"application-{run_id}"
    await activity_env(workflow_id=wf, activity_id="9", attempt=1).call(
        registry.open, str(run_id), "https://jobs.harness.example/acme/1")
    with pytest.raises(ApplicationError) as exc:
        await activity_env(workflow_id=wf, activity_id="7", attempt=1).call(
            browser_activities.fill_activity, fill_input(run_id))
    assert (exc.value.type, exc.value.non_retryable) == ("superseded", True)
    assert (await db.run(run_id))["status"] == "RUNNING"  # untouched


# ── the attempt lease (contract §7) ───────────────────────────────────────────

async def test_the_lease_only_moves_forward(db, activity_env) -> None:
    from yahbah.run_state import claim_submission
    from yahbah.workflows.activities.db_ops import fenced_update, take_lease

    posting, run_id = await running_run(db)
    wf = f"application-{run_id}"
    older = activity_env(workflow_id=wf, activity_id="12", attempt=1)
    newer = activity_env(workflow_id=wf, activity_id="12", attempt=2)
    await older.call(take_lease, str(run_id))
    await newer.call(take_lease, str(run_id))
    for call in (older.call(take_lease, str(run_id)), older.call(fenced_update, str(run_id))):
        with pytest.raises(ApplicationError) as exc:
            await call
        assert exc.value.type == "superseded"
    assert await older.call(claim_submission, str(run_id), wf, posting) is False
    row = await db.run(run_id)
    assert (row["lease_activity_seq"], row["lease_attempt"], row["submit_attempted_at"]) == (12, 2, None)
    later_stage = activity_env(workflow_id=wf, activity_id="14", attempt=1)
    assert await later_stage.call(claim_submission, str(run_id), wf, posting) is True


async def test_a_stale_tripwire_still_claims_the_job(db, activity_env) -> None:
    """record_possible_submission() marks the run only under the lease, but always claims the job."""
    from yahbah.run_state import record_possible_submission
    from yahbah.workflows.activities.db_ops import take_lease

    posting, run_id = await running_run(db)
    wf = f"application-{run_id}"
    await activity_env(workflow_id=wf, activity_id="20", attempt=1).call(take_lease, str(run_id))
    await activity_env(workflow_id=wf, activity_id="18", attempt=1).call(
        record_possible_submission, str(run_id), wf, posting)
    assert (await db.run(run_id))["submit_attempted_at"] is None
    assert await claims(db) == [{"posting_key": str(posting), "run_id": run_id}]


@pytest.mark.parametrize("args, run", [
    (("0b0e9c6e-6d1f-4c43-9a0f-0d6c2d7a1a11", "FILL"), "0b0e9c6e-6d1f-4c43-9a0f-0d6c2d7a1a11"),
    (({"run_id": "0b0e9c6e-6d1f-4c43-9a0f-0d6c2d7a1a11", "job_url": "u"},), "0b0e9c6e-6d1f-4c43-9a0f-0d6c2d7a1a11"),
    ((SimpleNamespace(run_id="0b0e9c6e-6d1f-4c43-9a0f-0d6c2d7a1a11"),), "0b0e9c6e-6d1f-4c43-9a0f-0d6c2d7a1a11"),
    (("not-a-run",), None),
    ((), None),
])
def test_the_lease_interceptor_finds_the_run_an_activity_acts_on(args, run) -> None:
    from yahbah.workflows.activities.lease import _run_id

    assert _run_id(args) == run


async def test_a_lost_browser_is_refilled_and_submit_uses_that_fills_own_baseline(
        db, activity_env, browser_activities, monkeypatch) -> None:
    _, run_id = await running_run(db)
    seen = {}

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        assert map_new_fields is None
        ctx.baseline = {"7": "after this re-fill"}
        return []

    async def submit_and_verify(session, board, ctx, *, claim, **kwargs):
        seen["baseline"] = ctx.baseline
        assert await claim()
        return SimpleNamespace(confirmation_url="u", confirmation_text="Thanks", confirmation_html="")

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    monkeypatch.setattr(browser_activities, "submit_and_verify", submit_and_verify)
    submit = submit_input(run_id)
    submit.baseline = {"7": "from the lost browser"}
    await activity_env(workflow_id=f"application-{run_id}").call(browser_activities.submit_activity, submit)
    assert seen["baseline"] == {"7": "after this re-fill"}
