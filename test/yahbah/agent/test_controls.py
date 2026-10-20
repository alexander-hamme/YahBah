"""The submit/advance classifier, the submit locator and the agent guard (agent/controls.py), in a real browser
on local pages. The full agent path (a scripted browser-use agent clicking each of the 7 SPA shapes) is in
test/invariants/task4_submit_boundary/test_agent_guard.py; this file covers the decisions themselves."""
import json

import pytest

from harness.browser import element_index, load
from yahbah.agent.controls import (
    BLOCKED,
    CLASSIFY_THIS_JS,
    FIND_SUBMIT_JS,
    FORM_PAGE_JS,
    FORM_PHASE_NO_NAVIGATION,
    GuardedTools,
    refusal_for,
)
from yahbah.agent.discover import looks_like_form
from yahbah.agent.tools import get_form_schema, run_js

pytestmark = pytest.mark.browser


async def classify(session, selector: str) -> dict:
    return json.loads(await run_js(
        session, f"({CLASSIFY_THIS_JS}).call(document.querySelector({json.dumps(selector)}))"))


async def locate(session) -> dict:
    return json.loads(await run_js(session, FIND_SUBMIT_JS))


@pytest.fixture
async def zoo(browser, fixture_server):
    await load(browser, fixture_server.url("synthetic/controls_zoo.html"))
    await get_form_schema(browser)  # stamps the field keys the locator scopes by
    return browser


# ── the classifier ────────────────────────────────────────────────────────────

async def test_every_control_on_the_zoo_page_is_classified_as_expected(zoo) -> None:
    expectations = json.loads(await run_js(zoo, """JSON.stringify([...document.querySelectorAll('[data-expect]')]
        .map((e) => [e.id, e.dataset.expect]))"""))
    assert len(expectations) > 30
    wrong = {}
    for element_id, want in expectations:
        got = (await classify(zoo, f"#{element_id}"))["kind"]
        if got != want:
            wrong[element_id] = (want, got)
    assert wrong == {}


async def test_a_click_on_what_is_inside_a_control_activates_the_control(zoo) -> None:
    assert (await classify(zoo, "#b-nested-span"))["kind"] == "submit"
    assert (await classify(zoo, "label[for=agree]"))["kind"] == "other"  # activates a checkbox
    assert (await classify(zoo, "#b-icon svg"))["kind"] == "submit"  # named by aria-label


async def test_options_and_inputs_are_never_buttons_whatever_they_say(zoo) -> None:
    for selector in ("#opt-continue", "#opt-next", "#agree", "#radio-submit", "#sel", "#notes", "#combo", "#first"):
        assert (await classify(zoo, selector))["kind"] == "other", selector


async def test_a_native_submit_is_never_other(zoo) -> None:
    info = await classify(zoo, "#unknown-label")
    assert (info["kind"], info["structural"]) == ("submit", True)  # unknown label, native submit: treated as submit
    info = await classify(zoo, "#native-next")
    assert (info["kind"], info["structural"]) == ("advance", True)
    info = await classify(zoo, "#b-submit")
    assert (info["kind"], info["structural"]) == ("submit", False)  # type=button: the label decides


async def test_form_page_mirrors_discover_looks_like_form(browser, fixture_server) -> None:
    for page in ("synthetic/controls_zoo.html", "greenhouse_konux.html", "synthetic/mock_ats.html",
                 "synthetic/no_cta.html", "synthetic/spa_submit_controls.html", "synthetic/job.html",
                 "synthetic/thanks.html"):
        await load(browser, fixture_server.url(page))
        assert await run_js(browser, FORM_PAGE_JS) == looks_like_form(await get_form_schema(browser)), page


# ── the locator ───────────────────────────────────────────────────────────────

async def test_the_locator_picks_the_native_submit_whose_label_reads_like_submitting(zoo) -> None:
    found = await locate(zoo)
    assert found["text"] == "Submit application" and found["kind"] == "submit" and found["disabled"] is False
    assert await run_js(zoo, "document.querySelector('[data-ja-field=\"submit-cta\"]').id") == "native-submit"
    assert "advance: Next" in found["found"] and "advance: Continue" in found["found"]


@pytest.mark.parametrize("html, text, kind, disabled", [
    # a step button is never the final submit, whatever its type
    ('<button type="submit">Continue</button>', None, None, None),
    ('<button type="button">Next</button><button type="button">Save for later</button>', None, None, None),
    ('<button type="submit">Next</button><button type="button">Back</button>', None, None, None),
    # the one native submit of unknown label is the submit
    ('<button type="submit">Go</button>', "Go", "submit", False),
    ('<button>Go</button>', "Go", "submit", False),
    ('<input type="submit" value="Send application">', "Send application", "submit", False),
    # a native button wins over a script one of the same label
    ('<button type="button">Submit</button><button type="submit">Submit</button>', "Submit", "submit", False),
    # enabled before disabled; a disabled one only when nothing else is there
    ('<button type="submit" disabled>Submit</button><button type="button">Apply</button>', "Apply", "submit", False),
    ('<button type="submit" disabled>Submit application</button>', "Submit application", "submit", True),
    # two unlabelled native submits are ambiguous: none
    ('<button type="submit">Go</button><button type="submit">Search</button>', None, None, None),
    # not buttons
    ('<a href="#">Submit</a><div>Apply</div>', None, None, None),
])
async def test_the_locator_on_small_forms(browser, pages, html, text, kind, disabled) -> None:
    url = pages.write("form.html", f"""<!doctype html><html><body><main><form onsubmit="return false">
      <input id="a" name="a" aria-label="A"><input id="b" name="b" aria-label="B"><input id="c" name="c" aria-label="C">
      {html}</form></main></body></html>""")
    await load(browser, url)
    await get_form_schema(browser)
    found = await locate(browser)
    assert (found["text"], found.get("kind"), found.get("disabled")) == (text, kind, disabled)


async def test_the_locator_finds_an_spa_submit_button_when_there_is_no_form_element(browser, pages) -> None:
    url = pages.write("spa.html", """<!doctype html><html><body><main><div id="app">
      <input aria-label="A"><input aria-label="B"><input aria-label="C">
      <div role="button" tabindex="0" onclick="">Back</div>
      <div role="button" tabindex="0" onclick="">Submit application</div></div></main></body></html>""")
    await load(browser, url)
    await get_form_schema(browser)
    assert (await locate(browser))["text"] == "Submit application"


async def test_a_submit_control_outside_the_form_that_holds_our_fields_is_not_the_form_s(browser, pages) -> None:
    """Scope is the form with our fields (as it always was): a button elsewhere on the page is not looked at,
    so submit_button_not_found rather than a click on something that may belong to another part of the page."""
    url = pages.write("outside.html", """<!doctype html><html><body><main>
      <form onsubmit="return false"><input aria-label="A"><input aria-label="B"><input aria-label="C"></form>
      <button type="button" onclick="">Submit application</button></main></body></html>""")
    await load(browser, url)
    await get_form_schema(browser)
    assert (await locate(browser))["text"] is None


async def test_the_locator_scopes_to_the_form_holding_our_fields(browser, pages) -> None:
    url = pages.write("two.html", """<!doctype html><html><body><main>
      <form id="news" onsubmit="return false"><input type="email" aria-label="Newsletter email">
        <button type="submit">Subscribe now and apply</button></form>
      <form id="app" onsubmit="return false">
        <input aria-label="A"><input aria-label="B"><input aria-label="C">
        <button type="submit">Submit application</button></form>
    </main></body></html>""")
    await load(browser, url)
    await get_form_schema(browser)
    assert (await locate(browser))["text"] == "Submit application"
    # the stamp moves with each search: exactly one control carries it
    assert await run_js(browser, "document.querySelectorAll('[data-ja-field=\"submit-cta\"]').length") == 1


# ── the guard ─────────────────────────────────────────────────────────────────

async def index_of(session, element_id: str) -> int:
    await session.get_browser_state_summary(include_screenshot=False)
    index = await element_index(session, id=element_id)
    assert index is not None, f"{element_id} is not indexed by browser-use"
    return index


@pytest.mark.parametrize("mode", ["form", "fix", "navigate"])
@pytest.mark.parametrize("element_id, blocked", [
    ("native-submit", True), ("untyped", True), ("input-submit", True), ("unknown-label", True),
    ("native-next", True), ("native-save", True), ("b-submit", True), ("b-continue", True), ("b-review", True),
    ("div-submit", True), ("link-apply", True), ("out-submit", True),
    ("b-back", False), ("b-attach", False), ("b-flyout", False), ("b-cancel", False), ("b-month", False),
    ("link-privacy", False), ("first", False), ("agree", False), ("sel", False),
])
async def test_the_guard_refuses_clicks_on_submit_and_advance_controls(zoo, mode, element_id, blocked) -> None:
    # the zoo is a form page, so even the navigating agent is held back
    refusal = await refusal_for(mode, zoo, "click", {"index": await index_of(zoo, element_id)})
    assert refusal == (BLOCKED if blocked else None)


async def test_the_navigating_agent_may_click_apply_until_the_page_shows_a_form(browser, fixture_server, pages) -> None:
    await load(browser, fixture_server.url("synthetic/job.html"))  # a job page: a link "Apply", no form
    state = await browser.get_browser_state_summary(include_screenshot=False)
    (apply_index,) = [i for i, n in state.dom_state.selector_map.items() if n.tag_name == "a"]
    assert await refusal_for("navigate", browser, "click", {"index": apply_index}) is None
    assert await refusal_for("form", browser, "click", {"index": apply_index}) == BLOCKED  # form phases never click it
    assert await refusal_for("fix", browser, "click", {"index": apply_index}) == BLOCKED
    # a coordinate click can hit anything: allowed only while there is no form
    assert await refusal_for("navigate", browser, "click", {"coordinate_x": 5, "coordinate_y": 5}) is None
    assert await refusal_for("form", browser, "click", {"coordinate_x": 5, "coordinate_y": 5}) == BLOCKED


@pytest.mark.parametrize("element_id, blocked", [
    ("native-submit", True), ("untyped-button", True), ("button-onclick", True), ("div-role-button", True),
    ("link-onclick", True),   # in the form: it takes input, so its buttons are the form's
    ("outside-form", False),  # a button elsewhere on a two-field page: nothing says what it belongs to (documented limit)
    ("full-name", False),
])
async def test_the_navigating_agent_is_held_back_by_a_small_form_of_its_own(browser, fixture_server, element_id, blocked) -> None:
    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))  # two fields: not a form page
    assert await run_js(browser, FORM_PAGE_JS) is False
    refusal = await refusal_for("navigate", browser, "click", {"index": await index_of(browser, element_id)})
    assert refusal == (BLOCKED if blocked else None)


async def test_a_forms_elsewhere_on_the_page_do_not_make_an_apply_link_a_submit_control(browser, pages) -> None:
    url = pages.write("jobpage.html", """<!doctype html><html><body><main><h1>Engineer</h1>
      <p><a id="apply" href="#">Apply</a></p>
      <form onsubmit="return false"><input aria-label="Name"><input aria-label="Email"><button id="sub" type="submit">Subscribe</button></form>
      </main></body></html>""")
    await load(browser, url)
    assert await run_js(browser, FORM_PAGE_JS) is False
    assert await refusal_for("navigate", browser, "click", {"index": await index_of(browser, "apply")}) is None
    assert await refusal_for("navigate", browser, "click", {"index": await index_of(browser, "sub")}) == BLOCKED
    await focus(browser, "input")
    assert await refusal_for("navigate", browser, "send_keys", {"keys": "Enter"}) == BLOCKED  # Enter in that form submits it


async def test_form_phases_may_not_leave_the_page(browser, fixture_server) -> None:
    await load(browser, fixture_server.url("synthetic/job.html"))
    for action, params in (("navigate", {"url": "https://example.test/"}), ("go_back", {}),
                           ("search", {"query": "jobs"}), ("close", {"tab_id": "ab12"})):
        assert await refusal_for("form", browser, action, params) == FORM_PHASE_NO_NAVIGATION
        assert await refusal_for("fix", browser, action, params) == FORM_PHASE_NO_NAVIGATION
        assert await refusal_for("navigate", browser, action, params) is None
    for action in ("scroll", "extract", "read_form_schema", "fill_field", "done", "wait"):
        assert await refusal_for("fix", browser, action, {}) is None


async def focus(session, selector: str) -> None:
    assert await run_js(session, f"(() => {{ const e = document.querySelector({json.dumps(selector)}); e.focus(); "
                                 f"return document.activeElement === e; }})()")


@pytest.mark.parametrize("selector, keys, blocked", [
    # implicit submission: Enter in a field of a form
    ("#first", "Enter", True), ("#first", "Return", True), ("#first", "hello\n", True), ("#email", "Shift+Enter", True),
    ("#first", "Control+Enter", True), ("#first", "Meta+Return", True),
    # a textarea takes Enter (a new line); a modified Enter submits there too
    ("#notes", "Enter", False), ("#notes", "Shift+Enter", False), ("#notes", "Control+Enter", True),
    # an open dropdown takes Enter to choose the highlighted option; a closed one does not
    ("#combo-open", "Enter", False), ("#combo", "Enter", True),
    # Enter or Space on a focused button clicks it
    ("#b-submit", "Enter", True), ("#b-submit", "Space", True), ("#native-submit", "Enter", True),
    ("#native-submit", " ", True), ("#b-continue", "Enter", True),
    ("#b-back", "Enter", True),  # not a submit control, but Enter on a plain button in a form is held back anyway
    # harmless keys
    ("#first", "Escape", False), ("#first", "ArrowDown", False), ("#first", "Tab", False),
    ("#first", "Control+a", False), ("#first", "Space", False), ("#first", "jane doe", False),
    ("#b-submit", "ArrowDown", False), ("#b-submit", "Tab", False),
])
async def test_the_guard_refuses_enter_and_space_that_would_submit(zoo, selector, keys, blocked) -> None:
    await focus(zoo, selector)
    for mode in ("form", "fix", "navigate"):  # the zoo is a form page, so the navigating agent is held back too
        assert await refusal_for(mode, zoo, "send_keys", {"keys": keys}) == (BLOCKED if blocked else None), \
            (mode, selector, keys)


async def test_enter_in_a_search_box_on_a_page_without_a_form_is_the_navigating_agents_to_press(browser, pages) -> None:
    url = pages.write("search.html", """<!doctype html><html><body><main><h1>Jobs</h1>
      <form onsubmit="return false"><input id="q" aria-label="Search jobs"><button type="submit">Search</button></form>
      </main></body></html>""")
    await load(browser, url)
    await focus(browser, "#q")
    assert await refusal_for("navigate", browser, "send_keys", {"keys": "Enter"}) is None
    assert await refusal_for("form", browser, "send_keys", {"keys": "Enter"}) == BLOCKED


async def test_typing_a_newline_is_pressing_enter(zoo) -> None:
    first = await index_of(zoo, "first")
    assert await refusal_for("fix", zoo, "input", {"index": first, "text": "Jane\n"}) == BLOCKED
    assert await refusal_for("fix", zoo, "input", {"index": first, "text": "Jane"}) is None
    notes = await index_of(zoo, "notes")
    assert await refusal_for("fix", zoo, "input", {"index": notes, "text": "line one\nline two"}) is None


async def test_a_refusal_comes_back_to_the_agent_as_an_error_and_nothing_is_clicked(zoo, fixture_server) -> None:
    from browser_use import ActionResult

    tools = GuardedTools()
    assert tools.mode == "form"  # the safe default
    index = await index_of(zoo, "b-submit")
    action = tools.registry.create_action_model()(click={"index": index})
    result = await tools.act(action=action, browser_session=zoo)
    assert isinstance(result, ActionResult) and result.error == BLOCKED
    assert await fixture_server.wait_events("clicked", 1, timeout=0.5) == 0

    # a click on something harmless goes through
    harmless = tools.registry.create_action_model()(click={"index": await index_of(zoo, "b-back")})
    assert (await tools.act(action=harmless, browser_session=zoo)).error is None
    assert await fixture_server.wait_events("clicked", 1) == 1


async def test_make_crew_builds_only_guarded_tools_and_run_agent_insists_on_them(zoo) -> None:
    from yahbah.agent.llm import make_crew, run_agent

    crew = make_crew()
    assert isinstance(crew.tools, GuardedTools)
    crew.tools = object()
    with pytest.raises(TypeError, match="GuardedTools"):
        await run_agent("anything", crew, zoo, max_steps=1)


def test_no_module_builds_a_browser_use_agent_outside_the_runner() -> None:
    """Every agent phase must go through run_agent, which is where the guard and the tripwire live."""
    import re
    from pathlib import Path

    import yahbah

    offenders = []
    for path in Path(yahbah.__file__).parent.rglob("*.py"):
        if path.name == "llm.py" and path.parent.name == "agent":
            continue
        text = path.read_text()
        if re.search(r"\bAgent\(", text) and re.search(r"from browser_use(\.\w+)* import [^\n]*\bAgent\b", text):
            offenders.append(str(path))
    assert offenders == []


@pytest.mark.parametrize("mode", ["form", "fix", "navigate"])
@pytest.mark.parametrize("element_id, blocked", [
    ("b-submit", True), ("native-submit", True), ("b-back", True), ("link-privacy", True),  # buttons and links
    ("agree", True), ("sel", True),                                                         # a key press there acts on it
    ("first", False), ("notes", False), ("combo", False),
])
async def test_typing_into_anything_but_a_text_control_is_refused_in_every_mode(zoo, mode, element_id, blocked) -> None:
    refusal = await refusal_for(mode, zoo, "input", {"index": await index_of(zoo, element_id), "text": "x y"})
    assert refusal == (BLOCKED if blocked else None)


async def test_space_on_a_focused_submit_button_in_a_same_origin_frame_is_refused(browser, pages) -> None:
    inner = ('<!doctype html><body><form onsubmit="return false"><input aria-label="A"><input aria-label="B"><input aria-label="C">'
             '<button type="button" id="inner-submit">Submit application</button></form></body>')
    url = pages.write("framed.html", "<!doctype html><html><body><main><h1>Apply</h1><iframe id='f' srcdoc='"
                      + inner.replace("'", "&#39;") + "'></iframe></main></body></html>")
    await load(browser, url)
    assert await run_js(browser, "(() => { const d = document.getElementById('f').contentDocument; "
                                 "d.getElementById('inner-submit').focus(); return d.activeElement.id; })()") == "inner-submit"
    for mode in ("form", "fix"):
        assert await refusal_for(mode, browser, "send_keys", {"keys": "Space"}) == BLOCKED
