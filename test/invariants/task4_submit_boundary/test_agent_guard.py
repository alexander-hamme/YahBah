"""Agents can't activate submit controls, and leaving the form is detected (roadmap A4, decision 4;
contract §2 `possibly_submitted`; audit QA F1, Forms F5).

The scripted agent drives the real fix-agent path: fill_form finds a required field it can't fill,
hands over to fix_with_agent, and the agent issues a real browser-use click on the target.
"""
import pytest

from harness.browser import load
from harness.contract import defect
from harness.forms import context_for, page_list
from yahbah.agent.boards import GENERIC
from yahbah.agent.discover import StageFailed
from yahbah.agent.fill import fill_form
from yahbah.agent.tools import get_form_schema

SHAPES = ["native-submit", "untyped-button", "enter-key", "button-onclick", "div-role-button", "link-onclick",
          "outside-form"]
GET_THROUGH_TODAY = {"button-onclick", "div-role-button", "link-onclick", "outside-form"}  # audit probe: 4 of 7


def _shape(shape: str):
    if shape in GET_THROUGH_TODAY:
        return pytest.param(shape, marks=defect(
            "A4", 4, f"a fix-agent click activates a {shape} submit control (the guard stops native submits only)"))
    return pytest.param(shape)


@pytest.mark.parametrize("shape", [_shape(s) for s in SHAPES])
async def test_an_agent_click_cannot_activate_a_submit_control(browser, fixture_server, agent_llm, shape) -> None:
    await load(browser, fixture_server.url("synthetic/spa_submit_controls.html"))
    ctx = context_for(await get_form_schema(browser), {"Full name": "Jane Doe"})  # Phone left empty
    if shape == "enter-key":
        agent_llm.click(id="full-name").send_keys("Enter")
    else:
        agent_llm.click(id=shape)
    agent_llm.done()
    try:
        await fill_form(browser, GENERIC, ctx)
    except StageFailed:
        pass  # refusing loudly is fine
    assert agent_llm.calls, "the fix agent was never reached"
    assert await page_list(browser, "__sent") == []
    assert [e.params for e in fixture_server.events_named("sent")] == []


@defect("contract §2 (possibly_submitted)", 4, "no tripwire: a fix-agent click that leaves the form ends as "
                                                "ordinary fill problems")
async def test_an_agent_click_that_leaves_the_form_raises_possibly_submitted(browser, fixture_server, agent_llm) -> None:
    await load(browser, fixture_server.url("synthetic/navigate_away.html"))
    ctx = context_for(await get_form_schema(browser), {"Full name": "Jane Doe"})  # Phone left empty
    agent_llm.click(id="back").done()
    with pytest.raises(StageFailed) as exc:
        await fill_form(browser, GENERIC, ctx)
    assert exc.value.reason == "possibly_submitted"
