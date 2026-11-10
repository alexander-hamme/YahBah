"""Batch-1 review, re-verification probes of the fixes (QA, 2026-10-20): the attempt lease
(contract §7, amended), the locked approved answers (§4) and the agent guard's focus handling (§6).
"""
import asyncio
import json
from pathlib import Path

import pytest
from temporalio import activity

from harness.activities import JOB_URL, fill_input, running_run
from harness.browser import load
from harness.contract import defect
from harness.forms import Claim, context_for, page_list, submit
from yahbah.agent.boards import GENERIC
from yahbah.agent.discover import StageFailed
from yahbah.agent.fill import FixOutcome, fill_form
from yahbah.agent.llm import make_crew, run_agent
from yahbah.agent.tools import get_form_schema, run_js

# A flaky required field (drops the first two writes, so the fix agent runs) and an optional comments box
# the approved answers leave empty.
FLAKY_WITH_COMMENTS = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Apply</title></head><body><main>
<form id="application" novalidate>
  <div><label for="first">First name *</label> <input id="first" required></div>
  <div><label for="colour">Favourite colour *</label> <input id="colour" required></div>
  <div><label for="city">City *</label> <input id="city" required></div>
  <div><label for="comments">Anything else?</label> <textarea id="comments"></textarea></div>
  <p><button type="submit" id="submit">Submit application</button></p>
</form></main>
<script>
let drops = 2;
document.getElementById('colour').addEventListener('blur', (e) => { if (drops-- > 0) e.target.value = ''; });
document.getElementById('application').addEventListener('submit', (e) => e.preventDefault());
</script></body></html>"""

# A form whose fields and submit button live in a cross-origin iframe (another host, same server).
CROSS_ORIGIN_FRAME = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Apply form</title></head><body>
<form id="application" novalidate>
  <div><label for="first">First name *</label> <input id="first" required></div>
  <div><label for="last">Last name *</label> <input id="last" required></div>
  <div><label for="city">City *</label> <input id="city" required></div>
  <button type="button" id="frame-submit">Submit application</button>
</form>
<script>
document.getElementById('frame-submit').addEventListener('click', () =>
  fetch('/__event?name=sent&shape=cross-origin-frame', { keepalive: true }).catch(() => {}));
</script></body></html>"""


# ── the lease: what a superseded attempt can still write ─────────────────────

@pytest.mark.db
async def test_an_attempt_that_lost_the_lease_cannot_replace_the_rounds_evidence(
        db, activity_env, browser_activities, registry, monkeypatch) -> None:
    from yahbah.workflows.activities.db_ops import take_lease

    _, run_id = await running_run(db)
    wf = f"application-{run_id}"
    stale, retry = activity_env(workflow_id=wf, activity_id="13", attempt=1), activity_env(
        workflow_id=wf, activity_id="13", attempt=2)
    retry_done = asyncio.Event()

    async def fill_form(session, board, ctx, map_new_fields=None, beat=None):
        if activity.info().attempt == 1:  # overran its timeout; carries on once its retry has finished
            await asyncio.wait_for(retry_done.wait(), 20)
            ctx.written = {"1": "stale"}
        else:
            ctx.written = {"1": "fresh"}
        return []

    monkeypatch.setattr(browser_activities, "fill_form", fill_form)
    monkeypatch.setattr(browser_activities, "submitted_values",
                        lambda ctx, values: [{"key": k, "written": ctx.written.get(k)} for k in ctx.fields])
    await stale.call(take_lease, str(run_id))  # what RunLeaseInterceptor does when an attempt starts
    await stale.call(registry.open, str(run_id), JOB_URL)
    stale_fill = asyncio.create_task(stale.call(browser_activities.fill_activity, fill_input(run_id)))
    await asyncio.sleep(0.2)
    await retry.call(take_lease, str(run_id))
    await retry.call(browser_activities.fill_activity, fill_input(run_id))
    retry_done.set()
    try:
        await stale_fill
    except Exception:
        pass  # failing as superseded is the expected outcome

    rows = await db.fetchall("SELECT path, artifact_metadata FROM application_artifact "
                             "WHERE artifact_type = 'submitted_values' ORDER BY created_at")
    latest = [r for r in rows if r["artifact_metadata"].get("review_round") == 1][-1]  # what GET /review reads
    assert json.loads(Path(latest["path"]).read_text())[0]["written"] == "fresh"


async def test_a_superseded_attempt_cannot_take_the_lease_back(db, activity_env) -> None:
    """Guard (holds today): taking the lease is monotonic, across attempts and across later activities."""
    from temporalio.exceptions import ApplicationError

    from yahbah.workflows.activities.db_ops import fenced_update, take_lease

    _, run_id = await running_run(db)
    wf = f"application-{run_id}"
    await activity_env(workflow_id=wf, activity_id="14", attempt=1).call(take_lease, str(run_id))

    async def stale_write() -> None:  # (an async def runs inside the activity context; a lambda's coroutine would not)
        await fenced_update(str(run_id), current_state="STALE")

    for activity_id, attempt in (("13", 2), ("13", 9), ("14", 0)):
        with pytest.raises(ApplicationError) as exc:
            await activity_env(workflow_id=wf, activity_id=activity_id, attempt=attempt).call(stale_write)
        assert exc.value.type == "superseded"
    with pytest.raises(ApplicationError):  # another workflow never gets in, lease or not
        await activity_env(workflow_id="application-other", activity_id="99", attempt=1).call(
            take_lease, str(run_id))
    row = await db.run(run_id)
    assert (row["current_state"], row["lease_activity_seq"], row["lease_attempt"]) == ("FILL", 14, 1)


# ── locked approved answers ───────────────────────────────────────────────────

async def type_comment(step) -> list[dict]:
    """An agent step that gets unapproved text into the comments box *past the tool guard* (which refuses `input`
    text that is not part of a planned answer, see the next test): these probes check the layers behind it, the
    baseline in problems_for and the submit gate. It then asks for the planned colour, which is harmless."""
    from yahbah.agent.fill import fill_text

    schema = await get_form_schema(step.session)
    comments = next(f["key"] for f in schema if f["label"].startswith("Anything else"))
    colour = next(f["key"] for f in schema if f["label"].startswith("Favourite colour"))
    await fill_text(step.session, comments, "I would also accept a lower salary.")
    return [{"fill_field": {"key": colour, "value": "Blue"}}]


@pytest.mark.browser
@pytest.mark.parametrize("first_fill", [False, True])
async def test_an_agent_cannot_type_text_that_is_not_part_of_a_planned_answer(
        browser, pages, agent_llm, llm, first_fill) -> None:
    """The tool guard: in the fix phase an `input` of unplanned text is refused (VALUE_NOT_PLANNED), in a locked
    re-fill (no mapper) as well as in a first fill (with one)."""
    from harness.browser import element_index

    await load(browser, pages.write("comments.html", FLAKY_WITH_COMMENTS))
    schema = await get_form_schema(browser)
    ctx = context_for(schema, {"First name": "Jane", "Favourite colour": "Blue", "City": "Boston"})

    async def input_comment(step):
        index = await element_index(step.session, id="comments")
        return [{"input": {"index": index, "text": "I would also accept a lower salary.", "clear": True}}]

    async def mapper(rows):
        return []

    agent_llm.step(input_comment).done(data={"fixed": True, "unresolved": []})
    await fill_form(browser, GENERIC, ctx, map_new_fields=mapper if first_fill else None)
    assert agent_llm.calls, "the fix agent was never reached"
    assert await run_js(browser, "document.getElementById('comments').value") == ""


@pytest.mark.browser
async def test_an_agent_cannot_add_unapproved_text_to_a_field_the_approval_left_empty(
        browser, pages, agent_llm, llm) -> None:
    await load(browser, pages.write("comments.html", FLAKY_WITH_COMMENTS))
    schema = await get_form_schema(browser)
    ctx = context_for(schema, {"First name": "Jane", "Favourite colour": "Blue", "City": "Boston"})  # approved

    agent_llm.step(type_comment).done(data={"fixed": True, "unresolved": []})
    await fill_form(browser, GENERIC, ctx, map_new_fields=None)
    assert agent_llm.calls, "the fix agent was never reached"
    typed = await run_js(browser, "document.getElementById('comments').value")
    assert typed, "the agent's text never reached the page (probe setup)"
    claim = Claim()
    try:
        await submit(browser, GENERIC, ctx, claim=claim)
    except StageFailed:
        pass  # refusing before the claim is the expected outcome
    assert claim.calls == 0, f"claimed with unapproved text in an optional field: {typed!r}"


@pytest.mark.browser
@pytest.mark.parametrize("handoff", ["baseline_carried", "baseline_missing"])
async def test_the_submit_activitys_fresh_context_still_refuses_unapproved_text(
        browser, pages, agent_llm, llm, handoff) -> None:
    """As above, but the gate runs on a new FillContext, the way activities/browser.submit_activity builds it
    (_fill_context from SubmitInput.field_mappings, ctx.written = SubmitInput.written, ctx.baseline =
    SubmitInput.baseline). baseline_carried is the real approve path (the re-fill's FillOutput.baseline): the
    agent's text must be caught as a change; baseline_missing: the gate must fail closed."""
    await load(browser, pages.write("comments.html", FLAKY_WITH_COMMENTS))
    schema = await get_form_schema(browser)
    approved = {"First name": "Jane", "Favourite colour": "Blue", "City": "Boston"}
    refill = context_for(schema, approved)

    agent_llm.step(type_comment).done(data={"fixed": True, "unresolved": []})
    await fill_form(browser, GENERIC, refill, map_new_fields=None)
    assert await run_js(browser, "document.getElementById('comments').value"), "probe setup: no agent text"

    fresh = context_for(schema, approved)  # what submit_activity has: the approved mappings ...
    fresh.written = dict(refill.written)   # ... and the re-fill's FillOutput.written
    if handoff == "baseline_carried":
        assert refill.baseline, "the re-fill took no baseline to hand over"
        fresh.baseline = dict(refill.baseline)  # ... and its FillOutput.baseline
    claim = Claim()
    with pytest.raises(StageFailed) as refused:
        await submit(browser, GENERIC, fresh, claim=claim)
    assert claim.calls == 0, "the submit activity's gate claimed a form holding unapproved text"
    expected = "Anything else" if handoff == "baseline_carried" else "baseline"
    assert refused.value.reason == "fill_failed" and expected in refused.value.detail, refused.value.detail


# ── planned values only, through every way an agent can write text (Phase 4 QA, two-pass answers) ──

def unplanned_text_step(path: str):
    """A fix-agent step that writes unplanned text into the comments box through `path`, then asks for the
    planned colour (harmless). `input_fragments` appends pieces that are each part of a planned answer
    (`clear: false`), so every single `input` passes text_allowed while the result is in no plan."""
    async def step(step):
        from harness.browser import element_index

        schema = await get_form_schema(step.session)
        colour = next(f["key"] for f in schema if f["label"].startswith("Favourite colour"))
        index = await element_index(step.session, id="comments")
        if path == "send_keys":
            writes = [{"click": {"index": index}}, {"send_keys": {"keys": "LowerSalaryIsFine"}}]
        else:
            writes = [{"input": {"index": index, "text": "Boston", "clear": True}},
                      {"input": {"index": index, "text": "Jane", "clear": False}}]
        return [*writes, {"fill_field": {"key": colour, "value": "Blue"}}]
    return step


@pytest.mark.browser
@pytest.mark.parametrize("path", ["send_keys", "input_fragments"])
@pytest.mark.parametrize("first_fill", [True, False], ids=["first_fill", "locked_refill"])
async def test_unplanned_text_in_a_field_without_a_plan_is_refused_or_reported_whatever_the_write_path(
        browser, pages, agent_llm, llm, first_fill, path) -> None:
    """Contract §4 "Planned values only": text no plan holds never sits on the form unnoticed. Either the
    guard refuses the write, or fill_form reports the field."""
    await load(browser, pages.write("comments.html", FLAKY_WITH_COMMENTS))
    schema = await get_form_schema(browser)
    ctx = context_for(schema, {"First name": "Jane", "Favourite colour": "Blue", "City": "Boston"})

    async def mapper(rows):
        return []

    agent_llm.step(unplanned_text_step(path)).done(data={"fixed": True, "unresolved": []})
    problems = await fill_form(browser, GENERIC, ctx, map_new_fields=mapper if first_fill else None)
    assert agent_llm.calls, "the fix agent was never reached"
    typed = await run_js(browser, "document.getElementById('comments').value")
    assert not typed or any("Anything else" in p for p in problems), (
        f"unplanned text {typed!r} sits in a field with no plan and fill_form reports {problems}")



# ── focus in a frame the guard can't read ────────────────────────────────────

@pytest.mark.browser
async def test_space_with_focus_in_a_cross_origin_frame_is_refused(browser, pages, fixture_server, agent_llm) -> None:
    """Guard: ACTIVE_INFO_JS can't look into a cross-origin frame, so it must fail closed (submit-boundary
    added no test for this). The frame is served from `localhost`, the page from 127.0.0.1."""
    frame_url = pages.write("frame.html", CROSS_ORIGIN_FRAME).replace("127.0.0.1", "localhost")
    page = pages.write("outer.html", f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Apply</title>
</head><body><main><h1>Apply</h1><iframe id="form-frame" src="{frame_url}" style="width:600px;height:400px"></iframe>
</main></body></html>""")
    await load(browser, page)
    await asyncio.sleep(0.5)  # the frame's document
    agent_llm.click(id="city").send_keys("Tab").send_keys("Space").done()
    history = await run_agent("Map the form.", make_crew(), browser, max_steps=5, output=FixOutcome, mode="form")
    assert agent_llm.calls and not agent_llm.missing, f"setup: the agent could not reach the frame ({agent_llm.missing})"
    assert await fixture_server.wait_events("sent", 1, timeout=0.5) == 0
    assert any(e and e.startswith("Blocked") for e in history.errors()), "the Space key was never refused"
