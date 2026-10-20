"""Batch-1 review, task 4 (submit boundary): an approval covers exactly the answers the user saw, and
agents can't activate a submit control (contract §4 approve path, §6 agent tripwire; roadmap A4, A7).

Found by QA's close-out review of 2026-10-20 as strict xfails; all fixed the same day, now regression tests.
"""
import pytest

from harness.browser import element_index, load
from harness.forms import Claim, context_for, page_list, submit
from yahbah.agent.boards import GENERIC
from yahbah.agent.discover import StageFailed
from yahbah.agent.fill import FixOutcome, fill_form, write_field
from yahbah.agent.llm import make_crew, run_agent
from yahbah.agent.tools import get_form_schema, run_js

pytestmark = pytest.mark.browser

# A required field that drops what is typed into it the first two times it loses focus (a flaky
# widget): code's write and its one retry fail, so the fix agent gets the field.
FLAKY_FIELD_FORM = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Apply</title></head><body><main>
<form id="application" novalidate>
  <div><label for="first">First name *</label> <input id="first" required></div>
  <div><label for="colour">Favourite colour *</label> <input id="colour" required></div>
  <div><label for="city">City *</label> <input id="city" required></div>
  <p><button type="submit" id="submit">Submit application</button></p>
</form></main>
<script>
let drops = 2;
document.getElementById('colour').addEventListener('blur', (e) => { if (drops-- > 0) e.target.value = ''; });
document.getElementById('application').addEventListener('submit', (e) => e.preventDefault());
</script></body></html>"""

# The submit button lives in a web component's shadow root, right after the last field.
SHADOW_SUBMIT_FORM = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Apply</title></head><body><main>
<form id="application" novalidate>
  <div><label for="first">First name *</label> <input id="first" required></div>
  <div><label for="last">Last name *</label> <input id="last" required></div>
  <div><label for="city">City *</label> <input id="city" required></div>
  <apply-bar></apply-bar>
</form></main>
<script>
window.__sent = [];
customElements.define('apply-bar', class extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = '<button type="button" id="shadow-submit">Submit application</button>';
    root.querySelector('button').addEventListener('click', () => {
      window.__sent.push('shadow-submit');
      fetch('/__event?name=sent&shape=shadow-submit', { keepalive: true }).catch(() => {});
    });
  }
});
</script></body></html>"""


async def value_of(session, element_id: str) -> str:
    return await run_js(session, f"document.getElementById({element_id!r}).value")


def by_label(schema: list[dict], label: str) -> str:
    return next(f["key"] for f in schema if f["label"] == label)


# ── an approval covers exactly the approved answers ──────────────────────────

async def test_an_approved_refill_never_reports_clean_with_an_unapproved_answer(browser, pages, agent_llm) -> None:
    await load(browser, pages.write("flaky.html", FLAKY_FIELD_FORM))
    schema = await get_form_schema(browser)
    ctx = context_for(schema, {"First name": "Jane", "Favourite colour": "Blue", "City": "Boston"})  # approved
    colour = by_label(schema, "Favourite colour")
    agent_llm.step(lambda step: [{"fill_field": {"key": colour, "value": "Purple"}}])
    agent_llm.done(data={"fixed": True, "unresolved": []})

    problems = await fill_form(browser, GENERIC, ctx, map_new_fields=None)
    assert agent_llm.calls, "the fix agent was never reached"
    shown = await value_of(browser, "colour")
    assert problems or shown == "Blue", f"clean fill reported while the page holds the unapproved {shown!r}"


async def test_the_submit_gate_refuses_a_form_that_differs_from_the_approved_answers(
        browser, fixture_server, llm) -> None:
    await load(browser, fixture_server.url("synthetic/mock_ats.html"))
    schema = await get_form_schema(browser)
    ctx = context_for(schema, {"First name": "Jane", "Last name": "Doe", "Email": "jane.doe@example.test"})
    for key in ctx.mappings:
        await write_field(browser, ctx, key, ctx.value_for(key))
    # What the fix agent's fill_field leaves behind (see the test above): the page and ctx.written say
    # "Janet", the approved mapping says "Jane".
    await write_field(browser, ctx, by_label(schema, "First name"), "Janet")
    claim = Claim()
    try:
        await submit(browser, GENERIC, ctx, claim=claim)
    except StageFailed:
        pass  # refusing before the claim is the expected outcome
    assert claim.calls == 0, "an unapproved answer was claimed for submission"
    assert await fixture_server.wait_events("submit", 1, timeout=0.5) == 0


# ── agents can't activate submit controls ─────────────────────────────────────

def type_into(agent_llm, element_id: str, text: str = "Jane Doe"):
    """One agent step: browser-use's `input` action on the element with that id."""
    async def step(ctx):
        index = await element_index(ctx.session, id=element_id)
        if index is None:
            agent_llm.missing.append({"id": element_id})
            return [agent_llm.done_action(ctx, success=False, text=f"{element_id} is not on the page")]
        return [{"input": {"index": index, "text": text, "clear": True}}]
    return agent_llm.step(step)


async def nothing_was_sent(session, fixture_server) -> None:
    assert await page_list(session, "__sent") == []
    assert await fixture_server.wait_events("sent", 1, timeout=0.5) == 0


@pytest.mark.parametrize("shape", ["button-onclick", "outside-form"])
async def test_a_fix_agent_typing_into_a_submit_control_cannot_activate_it(browser, fixture_server, agent_llm,
                                                                           shape) -> None:
    """The fix path (fill_form -> fix agent, mode "fix"). Native submits are cancelled at window level in
    this mode, so only the script-driven shapes get through."""
    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))
    ctx = context_for(await get_form_schema(browser), {"Full name": "Jane Doe"})  # Phone left empty
    type_into(agent_llm, shape).done()
    try:
        await fill_form(browser, GENERIC, ctx)
    except StageFailed:
        pass  # refusing loudly is fine
    assert agent_llm.calls and not agent_llm.missing
    await nothing_was_sent(browser, fixture_server)


@pytest.mark.parametrize("shape", ["native-submit", "button-onclick"])
async def test_a_form_mode_agent_typing_into_a_submit_control_cannot_activate_it(browser, fixture_server,
                                                                                 agent_llm, shape) -> None:
    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))
    type_into(agent_llm, shape).done()
    await run_agent("Map the form.", make_crew(), browser, max_steps=4, output=FixOutcome, mode="form")
    assert agent_llm.calls and not agent_llm.missing
    await nothing_was_sent(browser, fixture_server)


async def test_space_on_a_submit_button_inside_a_shadow_root_is_refused(browser, pages, fixture_server,
                                                                        agent_llm) -> None:
    await load(browser, pages.write("shadow.html", SHADOW_SUBMIT_FORM))
    agent_llm.click(id="city").send_keys("Tab").send_keys("Space").done()
    await run_agent("Map the form.", make_crew(), browser, max_steps=5, output=FixOutcome, mode="form")
    assert agent_llm.calls and not agent_llm.missing
    await nothing_was_sent(browser, fixture_server)


async def fix_agent_leaves_the_form(browser, fixture_server, agent_llm, db, activity_env, monkeypatch, *,
                                    cancel: bool):
    """The real fill_activity on the real page: the fix agent follows a link off the filled form (the
    tripwire's own case, test_agent_guard.py); with `cancel`, the user's cancel lands right after, before
    the agent loop ends. Returns (run_id, the error the activity raised)."""
    from types import SimpleNamespace

    from harness.activities import running_run
    from yahbah.agent import session as session_module
    from yahbah.schemas import FillInput
    from yahbah.workflows.activities import browser as browser_activities

    url = fixture_server.url("synthetic/navigate_away.html")
    await load(browser, url)
    schema = await get_form_schema(browser)
    ctx = context_for(schema, {"Full name": "Jane Doe"})  # Phone left empty: the fix agent runs
    _, run_id = await running_run(db)
    registry = session_module.SessionRegistry()
    registry._sessions[str(run_id)] = (browser, None)  # the run's live browser (no owner yet)
    monkeypatch.setattr(session_module.SessionRegistry, "_instance", registry)

    async def profile():
        return SimpleNamespace(resume_path="/nonexistent/resume.pdf", email="jane.doe@example.test")

    monkeypatch.setattr(browser_activities, "_load_profile", profile)
    env = activity_env(workflow_id=f"application-{run_id}")

    def cancel_now(step):
        env.cancel()  # lands at the agent loop's next await
        return [agent_llm.done_action(step)]

    agent_llm.click(id="back")
    if cancel:
        agent_llm.step(cancel_now)
    fields = [{"key": f["key"], "label": f["label"], "field_type": f["type"], "required": f["required"],
               "options": f.get("options") or []} for f in schema]
    with pytest.raises(BaseException) as raised:
        await env.call(browser_activities.fill_activity, FillInput(
            run_id=str(run_id), job_url=url, form_schema_dict={"fields": fields, "page_url": url, "board": "generic"},
            field_mappings=list(ctx.mappings.values()), job_description="", cover_letter_path=None))
    assert not agent_llm.missing, "the agent never clicked the link"
    return run_id, raised.value


async def possible_submission_recorded(db, run_id) -> None:
    assert (await db.run(run_id))["submit_attempted_at"] is not None, "a possible submission was not recorded"
    assert await db.fetchall("SELECT run_id FROM submission_claim") == [{"run_id": run_id}]


async def test_a_fix_agent_that_left_the_form_records_a_possible_submission(
        browser, fixture_server, agent_llm, db, activity_env, monkeypatch) -> None:
    """Guard (holds today), and the control for the cancellation case below."""
    run_id, error = await fix_agent_leaves_the_form(browser, fixture_server, agent_llm, db, activity_env,
                                                    monkeypatch, cancel=False)
    assert getattr(error, "type", None) == "possibly_submitted", repr(error)
    await possible_submission_recorded(db, run_id)


async def test_a_cancel_after_the_fix_agent_left_the_form_still_records_a_possible_submission(
        browser, fixture_server, agent_llm, db, activity_env, monkeypatch) -> None:
    run_id, _ = await fix_agent_leaves_the_form(browser, fixture_server, agent_llm, db, activity_env, monkeypatch,
                                                cancel=True)
    await possible_submission_recorded(db, run_id)


async def test_space_on_a_focused_top_level_submit_button_is_refused(browser, fixture_server, agent_llm) -> None:
    """Guard (holds today): the same key press on a button in the main document is refused."""
    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))
    await run_js(browser, "document.getElementById('button-onclick').focus()")
    agent_llm.send_keys("Space").done()
    await run_agent("Map the form.", make_crew(), browser, max_steps=4, output=FixOutcome, mode="form")
    await nothing_was_sent(browser, fixture_server)
