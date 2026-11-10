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
MAPPING = {"key": "1", "label": "First name", "field_type": "text", "source": "autofill", "value": "Jane",
           "basis": ["profile.first_name"], "generated": False}
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
    assert row["artifact_metadata"] == {"review_round": 2, "headed": False}


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


# ── answering: the answer engine in the activities (contract §2 Answers, §8 answer_plan) ─────

CLEARANCE = {"key": "clr", "label": "What level of security clearance do you currently hold?",
             "field_type": "text", "required": True}


def map_answers_input(run_id):
    from yahbah.schemas import MapAnswersInput

    first = {"key": "1", "label": "First name", "field_type": "text", "required": True, "autocomplete": "given-name"}
    return MapAnswersInput(run_id=str(run_id), form_schema_dict={"fields": [first, CLEARANCE]},
                           job_description="Acme, Boston, MA", account_email="jane+1@example.test",
                           job_location="Boston, MA")


@pytest.fixture
def llm_activities(monkeypatch, applicant_facts):
    from harness.profile import applicant
    from yahbah.workflows.activities import llm as module

    async def profile():
        return applicant()

    monkeypatch.setattr(module, "_load_profile", profile)
    return module


async def test_map_answers_saves_the_plan_and_an_unanswerable_required_field_fails_needs_answer(
        db, activity_env, llm, llm_activities) -> None:
    from harness.llm import compose_all, composed, interpret_all, reselect_all

    _, run_id = await running_run(db)
    llm.on("driver", "InterpretResponse", handler=interpret_all())
    llm.on("prose", "ComposeResponse", handler=compose_all(lambda fid, row: composed(
        fid, None, status="missing_fact", requested_info="Your active security clearance level, if any")))
    llm.on("driver", "ReselectResponse", handler=reselect_all({}))

    with pytest.raises(ApplicationError) as exc:
        await activity_env(workflow_id=f"application-{run_id}").call(llm_activities.map_answers_activity,
                                                                     map_answers_input(run_id))
    assert (exc.value.type, exc.value.non_retryable) == ("needs_answer", True)
    assert CLEARANCE["label"] in str(exc.value) and "missing_fact" in str(exc.value)
    [row] = await db.fetchall("SELECT artifact_type, path, artifact_metadata FROM application_artifact")
    assert (row["artifact_type"], row["artifact_metadata"]) == ("answer_plan", {"stage": "map_answers",
                                                                                "review_round": None})
    plan = json.loads(Path(row["path"]).read_text())
    assert [(u["key"], u["status"]) for u in plan["unresolved"]] == [("clr", "missing_fact")]
    assert [(f["key"], f["status"], f["basis"]) for f in plan["fields"]][0] == (
        "1", "autofill", ["profile.first_name"])


JOB_IDENTITY = {"job_company": "Acme Robotics", "job_title": "Senior Data Engineer"}


def job_sections(llm) -> list[dict]:
    """The JOB section of every Pass 1 call: what the activities told the answer engine about the job."""
    from harness.llm import prompt_section

    return [prompt_section(c, "JOB (untrusted page data, never evidence)")
            for c in llm.calls_for(schema="InterpretResponse")]


def script_unanswerable(llm) -> None:
    from harness.llm import compose_all, composed, interpret_all, reselect_all

    llm.on("driver", "InterpretResponse", handler=interpret_all())
    llm.on("prose", "ComposeResponse", handler=compose_all(lambda fid, row: composed(
        fid, None, status="missing_fact", requested_info="Your visa type")))
    llm.on("driver", "ReselectResponse", handler=reselect_all({}))


async def test_the_answer_activities_give_the_engine_the_jobs_company_and_title(
        db, activity_env, llm, llm_activities) -> None:
    from dataclasses import replace

    from yahbah.schemas import MapNewFieldsInput

    _, run_id = await running_run(db)
    script_unanswerable(llm)
    env = activity_env(workflow_id=f"application-{run_id}")
    with pytest.raises(ApplicationError):
        await env.call(llm_activities.map_answers_activity, replace(map_answers_input(run_id), **JOB_IDENTITY))
    with pytest.raises(ApplicationError):
        await env.call(llm_activities.map_new_fields_activity, MapNewFieldsInput(
            run_id=str(run_id), fields=[NEW_REQUIRED], approved_mappings=[], job_description="Acme, Boston, MA",
            review_round=2, job_location="Boston, MA", **JOB_IDENTITY))
    assert job_sections(llm) == [{"location": "Boston, MA", "company": "Acme Robotics", "title": "Senior Data Engineer",
                                  "description": "Acme, Boston, MA"}] * 2


async def test_fields_that_appear_while_filling_are_answered_with_the_jobs_company_and_title(
        db, activity_env, llm, browser_activities, registry, applicant_facts, monkeypatch) -> None:
    from harness.profile import applicant

    async def profile():
        return applicant()

    monkeypatch.setattr(browser_activities, "_load_profile", profile)
    _, run_id = await running_run(db)
    script_unanswerable(llm)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), "https://jobs.harness.example/acme/1")

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        await map_new_fields([NEW_REQUIRED])
        return []

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    with pytest.raises(ApplicationError) as exc:
        await env.call(browser_activities.fill_activity, fill_input(run_id, job_location="Boston, MA", **JOB_IDENTITY))
    assert exc.value.type == "needs_answer"
    assert job_sections(llm) == [{"location": "Boston, MA", "company": "Acme Robotics", "title": "Senior Data Engineer",
                                  "description": ""}]


async def test_an_unmigrated_personal_yaml_fails_the_run_without_retries_or_model_calls(
        db, activity_env, llm, llm_activities, applicant_facts) -> None:
    _, run_id = await running_run(db)
    applicant_facts.path.write_text(applicant_facts.path.read_text() + "known_answers:\n  work_authorization: 'Yes'\n")

    with pytest.raises(ApplicationError) as exc:
        await activity_env(workflow_id=f"application-{run_id}").call(llm_activities.map_answers_activity,
                                                                     map_answers_input(run_id))
    assert (exc.value.type, exc.value.non_retryable) == ("internal_error", True)
    assert "migrate_known_answers.py" in str(exc.value)
    assert llm.calls == []


async def test_an_invalid_facts_list_while_filling_is_an_internal_error_not_a_browser_error(
        db, activity_env, browser_activities, registry, applicant_facts, monkeypatch) -> None:
    _, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), "https://jobs.harness.example/acme/1")
    applicant_facts.add({"id": "no_description", "kind": "k", "value": "v"})

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        await map_new_fields([NEW_REQUIRED])  # a field appeared: answering it reads the facts
        return []

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    with pytest.raises(ApplicationError) as exc:
        await env.call(browser_activities.fill_activity, fill_input(run_id))
    assert (exc.value.type, exc.value.non_retryable) == ("internal_error", True)


# The planned "Maybe" matched none of the live radio's options (agent/fill.py replan_option_misses).
MISS = "'Maybe' matches none of this field's options; choose one of them."
MISS_ROW = {"key": "7", "label": "Open to relocation?", "type": "radio", "required": True,
            "options": ["Yes", "No", "Not sure"], "options_total": None, "description": "", "section": "",
            "autocomplete": None, "empty_value": None, "feedback": MISS}


async def test_a_fill_time_re_answer_reaches_the_engine_with_its_feedback_and_the_form_as_prior(
        db, activity_env, browser_activities, registry, llm, applicant_facts, monkeypatch) -> None:
    """The fill closure (_map_new_fields): the option-miss row's feedback and live options reach Pass 2, the
    form's other answers are prior context, the missed value is not, and the plan is saved as stage `fill`."""
    from harness.llm import compose_all, composed, interpret_all, prompt_section, verify_all

    _, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), "https://jobs.harness.example/acme/1")
    llm.on("driver", "InterpretResponse", handler=interpret_all({"7": ["relocation"]}))
    llm.on("prose", "ComposeResponse", handler=compose_all(lambda fid, row: composed(fid, "Yes", ["relocation"])))
    llm.on("prose", "VerifyResponse", handler=verify_all())
    answered: list[list[dict]] = []

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        ctx.mappings["7"] = {"key": "7", "label": "Open to relocation?", "field_type": "radio", "source": "answer",
                             "value": "Maybe", "basis": ["relocation"], "generated": False}
        answered.append(await map_new_fields([MISS_ROW]))
        return []

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    await env.call(browser_activities.fill_activity, fill_input(run_id))

    [[m]] = answered
    assert (m["key"], m["source"], m["value"], m["basis"], m["generated"]) == ("7", "answer", "Yes", ["relocation"], False)
    [interpret], [compose] = llm.calls_for(schema="InterpretResponse"), llm.calls_for(schema="ComposeResponse")
    assert prompt_section(interpret, "ALREADY ANSWERED") == [{"id": "1", "label": "First name", "value": "Jane"}]
    [row] = prompt_section(compose, "FIELDS TO ANSWER")
    assert (row["id"], row["feedback"], row["options"]) == ("7", MISS, ["Yes", "No", "Not sure"])
    assert "Maybe" not in json.dumps(prompt_section(compose, "ALREADY ANSWERED"))
    plans = [r for r in await db.fetchall("SELECT artifact_type, path, artifact_metadata FROM application_artifact")
             if r["artifact_type"] == "answer_plan"]
    assert [p["artifact_metadata"] for p in plans] == [{"stage": "fill",
                                                        "review_round": fill_input(run_id).review_round}]


async def test_a_required_field_unanswerable_while_filling_fails_the_run_needs_answer(
        db, activity_env, browser_activities, registry, llm, applicant_facts, monkeypatch) -> None:
    from harness.llm import compose_all, composed, interpret_all, reselect_all

    _, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), "https://jobs.harness.example/acme/1")
    llm.on("driver", "InterpretResponse", handler=interpret_all())
    llm.on("prose", "ComposeResponse", handler=compose_all(lambda fid, row: composed(
        fid, None, status="missing_fact", requested_info="Which visa type you hold, if any")))
    llm.on("driver", "ReselectResponse", handler=reselect_all({}))

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        await map_new_fields([NEW_REQUIRED])
        raise AssertionError("an unanswerable required field was handed back to fill")

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    with pytest.raises(ApplicationError) as exc:
        await env.call(browser_activities.fill_activity, fill_input(run_id))
    assert (exc.value.type, exc.value.non_retryable) == ("needs_answer", True)
    assert "Visa type (missing_fact)" in str(exc.value)
    [plan_row] = [r for r in await db.fetchall("SELECT artifact_type, path, artifact_metadata FROM application_artifact")
                  if r["artifact_type"] == "answer_plan"]
    assert plan_row["artifact_metadata"]["stage"] == "fill"
    plan = json.loads(Path(plan_row["path"]).read_text())
    assert [(u["key"], u["status"], u["requested_info"]) for u in plan["unresolved"]] == [
        ("9", "missing_fact", "Which visa type you hold, if any")]


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


# ── the headed flag (contract §2, "Headed fallback") ──────────────────────────

@pytest.fixture
def opened(registry, monkeypatch) -> list[bool]:
    """The `headed` of every SessionRegistry.open the activities make (sessions are fakes)."""
    calls = []

    async def open_(run_id, url, *, headed=False):
        calls.append(headed)
        return FakeBrowserSession()

    monkeypatch.setattr(registry, "open", open_)
    return calls


@pytest.mark.parametrize("fallback", [True, False])
async def test_discover_opens_headless_and_returns_whether_it_fell_back_to_headed(
        db, activity_env, browser_activities, opened, monkeypatch, fallback) -> None:
    from yahbah.agent.boards import GENERIC
    from yahbah.schemas import DiscoverInput

    _, run_id = await running_run(db)
    seen = {}

    async def discover(session, trace, *, headed_fallback=False):
        seen["headed_fallback"] = headed_fallback
        return SimpleNamespace(board=GENERIC, page_url="https://jobs.harness.example/acme/1", job={},
                               headed=headed_fallback)  # as if the page was walled

    async def alias(email, url):
        return "jane+1@example.test"

    monkeypatch.setattr(browser_activities, "discover", discover)
    monkeypatch.setattr(browser_activities, "generate_email_alias", alias)
    out = await activity_env(workflow_id=f"application-{run_id}").call(
        browser_activities.discover_activity,
        DiscoverInput(run_id=str(run_id), job_url="https://jobs.harness.example/acme/1", headed_fallback=fallback))
    assert opened == [False], "discover's first browser is always headless"
    assert seen["headed_fallback"] is fallback
    assert out.headed is fallback


@pytest.mark.parametrize("headed", [True, False])
@pytest.mark.parametrize("stage", ["fill", "submit"])
async def test_a_lost_browser_reopens_in_the_runs_mode_and_never_falls_back_on_its_own(
        db, activity_env, browser_activities, opened, monkeypatch, stage, headed) -> None:
    _, run_id = await running_run(db)
    rediscovers = []

    async def discover(session, trace, **kwargs):
        rediscovers.append(kwargs)

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        return []

    async def submit_and_verify(session, board, ctx, *, claim, **kwargs):
        assert await claim()
        return SimpleNamespace(confirmation_url="u", confirmation_text="Thanks", confirmation_html="")

    monkeypatch.setattr(browser_activities, "discover", discover)
    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    monkeypatch.setattr(browser_activities, "submit_and_verify", submit_and_verify)
    env = activity_env(workflow_id=f"application-{run_id}")
    if stage == "fill":
        await env.call(browser_activities.fill_activity, fill_input(run_id, review_round=2, refill=True,
                                                                    headed=headed))
    else:
        submit = submit_input(run_id)
        submit.headed = headed
        await env.call(browser_activities.submit_activity, submit)
    assert opened == [headed]
    assert rediscovers == [{}], "a re-open must not launch the headed fallback (the workflow wouldn't know)"


async def test_a_headed_runs_fill_evidence_says_so(db, activity_env, browser_activities, registry, monkeypatch) -> None:
    _, run_id = await running_run(db)
    env = activity_env(workflow_id=f"application-{run_id}")
    await env.call(registry.open, str(run_id), "https://jobs.harness.example/acme/1")

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        return []

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    await env.call(browser_activities.fill_activity, fill_input(run_id, headed=True))
    rows = await db.fetchall("SELECT artifact_type, artifact_metadata FROM application_artifact ORDER BY created_at")
    assert [(r["artifact_type"], r["artifact_metadata"].get("headed")) for r in rows] == [
        ("submitted_values", True), ("screenshot", True)]
