"""Code evidence decides "submitted" and re-clicks (roadmap A3; contract §6; audit Forms F1/F2.5, QA F3).

The real fill_form and submit_and_verify run against synthetic/mock_ats.html over the fixture
server, with the post-submit judge scripted. Clicks are counted by the page (window.__clicks) and
by the server (`submit` events).
"""
import pytest

from harness.browser import load
from harness.contract import defect
from harness.forms import Claim, cap_submit_waits, filled_form, page_count, page_judge, submit
from harness.llm import verdict
from yahbah.agent.boards import GENERIC
from yahbah.agent.discover import StageFailed

VALUES = {"First name": "Jane", "Last name": "Doe", "Email": "jane.doe@example.test"}
JUDGE = (("driver", "vision"), "PostSubmitState")


@pytest.fixture
def mock_ats(browser, fixture_server, agent_llm, monkeypatch):
    """Open mock_ats.html with the given behaviour and fill it; the fix agent, if reached, is scripted
    to finish at once. One verification round, and fixed waits capped at 0.5 s: on these pages the
    state after a click is final at once (the slow-success test restores real waits)."""
    from yahbah.config import settings

    monkeypatch.setattr(settings, "submit_max_attempts", 1)
    cap_submit_waits(monkeypatch)

    async def open_form(query: str):
        await load(browser, fixture_server.url(f"synthetic/mock_ats.html?{query}"))
        return await filled_form(browser, VALUES)
    return open_form


async def clicks(browser, fixture_server) -> tuple[int, int]:
    """(submits the page counted, submits the server saw)."""
    on_page = await page_count(browser, "__clicks")
    return on_page, await fixture_server.wait_events("submit", on_page, timeout=1.0)


@defect("A3", 4, "a slow success is judged needs_fix after the fixed 3 s wait, and submit is clicked again")
async def test_a_slow_success_gets_exactly_one_click_and_succeeds(browser, fixture_server, llm, mock_ats, monkeypatch) -> None:
    import asyncio

    import yahbah.agent.submit as submit_module
    from yahbah.config import settings

    monkeypatch.setattr(settings, "submit_max_attempts", 4)
    monkeypatch.setattr(submit_module, "asyncio", asyncio)  # real waits: timing is the point here
    ctx = await mock_ats("success=delay:6000")
    llm.on(*JUDGE, handler=page_judge(browser))
    result = await submit(browser, GENERIC, ctx, claim=(claim := Claim()))
    assert "Thank you" in result.confirmation_text
    assert await clicks(browser, fixture_server) == (1, 1)
    assert claim.calls == 1


@defect("A3", 4, "'submitted' is accepted while the form, its fields and the submit button are all still there")
async def test_a_click_that_did_nothing_is_not_success_even_if_the_judge_says_submitted(browser, fixture_server, llm, mock_ats) -> None:
    ctx = await mock_ats("success=never")
    llm.on(*JUDGE, handler=lambda call: verdict("submitted", reasoning="The application appears to be submitted."))
    with pytest.raises(StageFailed) as exc:
        await submit(browser, GENERIC, ctx, claim=Claim())
    assert exc.value.reason == "submit_unverified"
    assert await clicks(browser, fixture_server) == (1, 1)


@defect("A3", 4, "a needs_fix verdict re-clicks submit with no code-detected error on the page")
async def test_needs_fix_without_code_detected_errors_does_not_click_again(browser, fixture_server, llm, mock_ats) -> None:
    ctx = await mock_ats("success=never")
    llm.on(*JUDGE, handler=lambda call: verdict("needs_fix", ["The phone number format looks invalid."]))
    with pytest.raises(StageFailed):
        await submit(browser, GENERIC, ctx, claim=Claim())
    assert await clicks(browser, fixture_server) == (1, 1)


@defect("A3", 4, "page text ('thank you') decides success: the judge reads it and code doesn't check")
async def test_thank_you_text_on_an_unsubmitted_form_is_not_success(browser, fixture_server, llm, mock_ats) -> None:
    ctx = await mock_ats("success=never&banner=thanks")
    llm.on(*JUDGE, handler=lambda call: verdict(
        "submitted", reasoning="The page says: Thank you! Your application has been received."))
    with pytest.raises(StageFailed) as exc:
        await submit(browser, GENERIC, ctx, claim=Claim())
    assert exc.value.reason == "submit_unverified"
    assert await clicks(browser, fixture_server) == (1, 1)


@defect("A3", 4, "the last round's re-click is never checked: submit_unverified is raised without looking")
async def test_the_final_rounds_click_is_followed_by_an_evidence_pass(browser, fixture_server, llm, mock_ats) -> None:
    """Round 1 sees a code-detected validation error (a legitimate re-click); the second click succeeds."""
    ctx = await mock_ats("success=nth:2&errors=first")
    llm.on(*JUDGE, handler=page_judge(browser))
    result = await submit(browser, GENERIC, ctx, claim=Claim())
    assert "Thank you" in result.confirmation_text
    assert await clicks(browser, fixture_server) == (2, 2)


@defect("contract §6", 4, "the submission is recorded before the submit button is located")
async def test_no_claim_is_made_when_the_submit_button_is_not_found(browser, fixture_server, llm) -> None:
    await load(browser, fixture_server.url("synthetic/no_cta.html"))
    ctx = await filled_form(browser, {"First name": "Jane", "Email": "jane.doe@example.test"})
    claim = Claim()
    with pytest.raises(StageFailed) as exc:
        await submit(browser, GENERIC, ctx, claim=claim)
    assert exc.value.reason == "submit_button_not_found"
    assert claim.calls == 0
    assert fixture_server.events_named("click") == []


@defect("contract §6", 4, "submit_and_verify clicks whether or not the submission claim was granted")
async def test_a_denied_claim_means_no_click(browser, fixture_server, llm, mock_ats) -> None:
    ctx = await mock_ats("success=instant")
    llm.on(*JUDGE, handler=page_judge(browser))  # only reached if it clicks anyway
    claim = Claim(granted=False)
    with pytest.raises(StageFailed) as exc:
        await submit(browser, GENERIC, ctx, claim=claim)
    assert exc.value.reason == "claim_denied"
    assert claim.calls == 1
    assert await clicks(browser, fixture_server) == (0, 0)
