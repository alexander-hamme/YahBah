"""Every agent phase runs guarded, and a fix agent that leaves the form trips the wire (agent/controls.py,
agent/llm.py:run_agent). The fix phase with all 7 SPA submit shapes is in
test/invariants/task4_submit_boundary/test_agent_guard.py; here: the other phases, at their real call sites, and
the tripwire's own conditions."""
import pytest

from harness.browser import load
from harness.forms import context_for, page_list
from yahbah.agent.boards import GENERIC
from yahbah.agent.controls import check_still_on_form, snapshot
from yahbah.agent.discover import StageFailed, Trace, discover, map_form, read_dropdowns
from yahbah.agent.fill import FixOutcome
from yahbah.agent.llm import make_crew, run_agent
from yahbah.agent.tools import get_form_schema, run_js

pytestmark = pytest.mark.browser

SHAPES = ["native-submit", "untyped-button", "enter-key", "button-onclick", "div-role-button", "link-onclick",
          "outside-form"]


def act_on(agent_llm, shape: str) -> None:
    if shape == "enter-key":
        agent_llm.click(id="full-name").send_keys("Enter")
    else:
        agent_llm.click(id=shape)
    agent_llm.done()


async def nothing_was_sent(browser, fixture_server) -> None:
    assert await page_list(browser, "__sent") == []
    assert [e.params for e in fixture_server.events_named("sent")] == []


# ── the guard in every phase ──────────────────────────────────────────────────

@pytest.mark.parametrize("shape", SHAPES)
async def test_an_agent_working_on_the_form_cannot_activate_any_submit_shape(browser, fixture_server, agent_llm, shape) -> None:
    """mode "form": the mapping and dropdown agents. (The fix agent, mode "fix", is in task4/test_agent_guard.)"""
    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))
    act_on(agent_llm, shape)
    await run_agent("Do nothing.", make_crew(), browser, max_steps=4, output=FixOutcome, mode="form")
    assert agent_llm.calls and not agent_llm.missing
    await nothing_was_sent(browser, fixture_server)


async def test_the_default_mode_is_the_guarded_one(browser, fixture_server, agent_llm) -> None:
    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))
    act_on(agent_llm, "button-onclick")
    await run_agent("Do nothing.", make_crew(), browser, max_steps=4, output=FixOutcome)
    await nothing_was_sent(browser, fixture_server)


@pytest.mark.parametrize("shape", ["button-onclick", "div-role-button"])
async def test_the_mapping_agent_cannot_submit(browser, fixture_server, agent_llm, shape) -> None:
    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))
    act_on(agent_llm, shape)
    with pytest.raises(StageFailed):  # the script maps nothing: form_unmapped is the expected end
        await map_form(browser, GENERIC, Trace(), force_tier="agent")
    assert agent_llm.calls
    await nothing_was_sent(browser, fixture_server)


@pytest.mark.parametrize("shape", ["button-onclick", "link-onclick"])
async def test_the_dropdown_agent_cannot_submit(browser, fixture_server, agent_llm, shape) -> None:
    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))
    act_on(agent_llm, shape)
    field = {"key": "none-such", "label": "Country", "type": "combobox", "options": []}
    await read_dropdowns(browser, [field], GENERIC, Trace())  # no key on the page: the agent is asked
    assert agent_llm.calls
    await nothing_was_sent(browser, fixture_server)


async def test_the_navigation_agent_clicks_through_to_the_form_and_stops_at_its_submit_controls(
        browser, fixture_server, pages, agent_llm, llm) -> None:
    form = (fixture_server.mounts["/"] / "synthetic" / "spa_submit_controls.html").read_text().replace(
        '<div><label for="phone">', '<div><label for="city">City</label> <input id="city" name="city"></div>\n'
                                    '    <div><label for="phone">')
    pages.write("form.html", form)
    start = pages.write("start.html", '<!doctype html><html><head><title>Acme</title></head><body><main><h1>Engineer</h1>'
                                      '<p><a id="begin" href="form.html">Begin</a></p></main></body></html>')
    await load(browser, start)
    llm.on("driver", "NavDecision", reply={"reasoning": "unclear", "state": "unknown", "element_index": None})
    agent_llm.click(id="begin").click(id="button-onclick").click(id="div-role-button").done()
    result = await discover(browser, Trace())

    assert result.page_url.endswith("form.html")  # the link was clickable: it is no submit control
    await nothing_was_sent(browser, fixture_server)  # the controls on the form were not
    assert agent_llm.missing == []


# ── the tripwire ──────────────────────────────────────────────────────────────

@pytest.fixture
async def form(browser, fixture_server):
    await load(browser, fixture_server.url("synthetic/mock_ats.html?success=never"))
    return browser


async def tripped(browser, before, settle: float = 0.4) -> str | None:
    try:
        await check_still_on_form(browser, before, settle_seconds=settle)
    except StageFailed as exc:
        assert exc.reason == "possibly_submitted"
        return exc.detail
    return None


async def test_the_same_form_does_not_trip(form) -> None:
    before = await snapshot(form)
    assert before.controls == 3 and before.token
    assert await tripped(form, before) is None


async def test_changes_that_leave_the_form_in_place_do_not_trip(form) -> None:
    before = await snapshot(form)
    await run_js(form, "location.hash = '#step-2'; document.getElementById('first').value = 'x'; "
                       "document.getElementById('email').focus(); 1")
    assert await tripped(form, before) is None


async def test_a_form_replaced_by_other_content_trips(form) -> None:
    before = await snapshot(form)
    await run_js(form, "document.getElementById('app').innerHTML = '<h1>Thanks</h1>'; 1")
    assert "fields are gone" in await tripped(form, before)


async def test_a_reload_trips_even_to_the_same_url(form) -> None:
    before = await snapshot(form)
    await run_js(form, "setTimeout(() => location.reload(), 0); 1")
    assert "document was replaced" in await tripped(form, before, settle=1.0)


async def test_a_navigation_trips(form, fixture_server) -> None:
    before = await snapshot(form)
    await run_js(form, f"setTimeout(() => {{ location.href = {fixture_server.url('synthetic/thanks.html')!r}; }}, 0); 1")
    assert await tripped(form, before, settle=1.5)


async def test_a_history_api_url_change_trips(form) -> None:
    before = await snapshot(form)
    await run_js(form, "history.pushState({}, '', '/somewhere/else'); 1")
    assert "URL changed" in await tripped(form, before)


async def test_a_link_that_opened_another_tab_does_not_trip_and_focus_returns_to_the_form(form, fixture_server) -> None:
    """(window.open from a script hangs headless Chrome behind browser-use for a minute; a new-tab navigation is
    what browser-use itself does for a link that opens one.)"""
    from browser_use.browser.events import CloseTabEvent, NavigateToUrlEvent

    before = await snapshot(form)
    await form.event_bus.dispatch(NavigateToUrlEvent(url=fixture_server.url("synthetic/job.html"), new_tab=True))
    (new_tab,) = [t for t in await form.get_tabs() if t.target_id != before.target_id]
    assert form.agent_focus_target_id == new_tab.target_id
    try:
        assert await tripped(form, before) is None
        assert form.agent_focus_target_id == before.target_id
    finally:
        await form.event_bus.dispatch(CloseTabEvent(target_id=new_tab.target_id))


async def test_a_fix_agent_click_on_a_plain_link_that_leaves_the_form_raises_possibly_submitted_through_fill_form(
        browser, fixture_server, agent_llm) -> None:
    """The invariant in task4/test_agent_guard, with the detail the activity will record."""
    from yahbah.agent.fill import fill_form

    await load(browser, fixture_server.url("synthetic/navigate_away.html"))
    ctx = context_for(await get_form_schema(browser), {"Full name": "Jane Doe"})
    agent_llm.click(id="back").done()
    with pytest.raises(StageFailed) as exc:
        await fill_form(browser, GENERIC, ctx)
    assert exc.value.reason == "possibly_submitted" and "left the form" in exc.value.detail


async def test_a_fix_agent_that_stays_on_the_form_returns_normally_and_releases_the_submit_guard(
        browser, fixture_server, agent_llm) -> None:
    from yahbah.agent.fill import fill_form

    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))
    ctx = context_for(await get_form_schema(browser), {"Full name": "Jane Doe"})
    agent_llm.click(id="full-name").done()
    problems = await fill_form(browser, GENERIC, ctx)
    assert any("Phone" in p for p in problems)  # the agent cannot invent it: reported, not raised
    # released: a native submit now reaches the page's handler
    await run_js(browser, "document.getElementById('application').requestSubmit(); 1")
    assert await page_list(browser, "__sent") == ["enter-key"]
