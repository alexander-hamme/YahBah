"""Self-tests of the browser pieces: pages over HTTP are indexed, the scripted agent drives the real
browser-use agent loop, and the scripted judge drives the real submit loop."""
from harness.browser import element_index, load
from harness.forms import Claim, filled_form, page_count, submit
from harness.llm import verdict
from yahbah.agent.boards import GENERIC

PAGE = """<!doctype html><html><body><main><h1>Harness</h1>
<p><button id="b" onclick="window.__clicks = (window.__clicks || 0) + 1">Count</button></p>
</main></body></html>"""


async def test_pages_over_http_are_indexed_for_clicks(browser, pages) -> None:
    await load(browser, pages.write("p.html", PAGE))
    await browser.get_browser_state_summary(include_screenshot=False)
    assert await element_index(browser, id="b") is not None  # about:blank would give an empty map


async def test_scripted_agent_drives_the_real_agent_loop(browser, pages, agent_llm) -> None:
    from yahbah.agent.fill import FixOutcome
    from yahbah.agent.llm import make_crew, run_agent

    await load(browser, pages.write("p.html", PAGE))
    agent_llm.click(id="b").click(id="b").done()
    history = await run_agent("press the button twice", make_crew(), browser, max_steps=5, output=FixOutcome)
    assert await page_count(browser, "__clicks") == 2
    assert history.is_done() and agent_llm.missing == []
    assert {c.role for c in agent_llm.calls} == {"driver"}


async def test_scripted_judge_drives_the_real_submit_loop(browser, fixture_server, llm) -> None:
    await load(browser, fixture_server.url("synthetic/mock_ats.html?success=instant"))
    ctx = await filled_form(browser, {"First name": "Jane", "Last name": "Doe", "Email": "jane@example.test"})
    llm.on(("driver", "vision"), "PostSubmitState", handler=lambda call: verdict("submitted"))
    result = await submit(browser, GENERIC, ctx, claim=(claim := Claim()))
    assert "Thank you" in result.confirmation_text
    assert await page_count(browser, "__clicks") == 1 and claim.calls == 1
    # (beacons are served by concurrent threads: compare counts, never their order)
    assert await fixture_server.wait_events("confirmed", 1) == 1 and len(fixture_server.events_named("submit")) == 1
