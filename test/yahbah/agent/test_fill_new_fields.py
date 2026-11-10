"""Fields that appear while filling (fill_form / fill_new_fields in agent/fill.py): mapped when there is a mapper,
otherwise recorded on ctx.unmapped_new_fields for the caller to send back to review (contract run-lifecycle §4)."""
import pytest

from harness.browser import load
from harness.forms import context_for
from yahbah.agent.boards import GENERIC
from yahbah.agent.fill import fill_form
from yahbah.agent.tools import get_form_schema

pytestmark = pytest.mark.browser

# "Do you need sponsorship?" = Yes reveals two follow-ups: a required free-text one and a required select;
# "Newsletter" = Yes reveals an optional one.
PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Apply</title></head><body><main>
<form id="application" novalidate>
  <div><label for="first">First name *</label> <input id="first" required></div>
  <div><label for="sponsor">Do you need sponsorship? *</label>
    <select id="sponsor" required><option value="">Select...</option><option>Yes</option><option>No</option></select></div>
  <div><label for="news">Newsletter</label>
    <select id="news"><option value="">Select...</option><option>Yes</option><option>No</option></select></div>
  <div id="sponsor-more" hidden>
    <div><label for="why">Please explain *</label> <input id="why" required></div>
    <div><label for="visa">Visa type *</label>
      <select id="visa" required><option value="">Select...</option><option>H-1B</option><option>L-1</option></select></div>
  </div>
  <div id="news-more" hidden><div><label for="freq">How often?</label> <input id="freq"></div></div>
  <p><button type="submit">Submit application</button></p>
</form></main>
<script>
const show = (sel, id, when) => document.getElementById(sel).addEventListener('change', (e) => {
  document.getElementById(id).hidden = e.target.value !== when; });
show('sponsor', 'sponsor-more', 'Yes'); show('news', 'news-more', 'Yes');
</script></body></html>"""

ANSWERS = {"First name": "Jane", "Do you need sponsorship?": "Yes", "Newsletter": "Yes"}


async def mapped_ctx(browser, pages):
    await load(browser, pages.write("reveal.html", PAGE))
    return context_for(await get_form_schema(browser), ANSWERS)


async def test_without_a_mapper_revealed_required_fields_are_recorded_not_failed_over(browser, pages) -> None:
    ctx = await mapped_ctx(browser, pages)
    problems = await fill_form(browser, GENERIC, ctx)  # map_new_fields=None: nothing may be mapped now

    assert problems == []  # a field nobody answered is the caller's decision, not a fill problem
    rows = {r["label"]: r for r in ctx.unmapped_new_fields}
    assert set(rows) == {"Please explain", "Visa type"}  # the optional "How often?" is not asked about
    why, visa = rows["Please explain"], rows["Visa type"]
    assert (why["type"], why["required"], why["options"]) == ("text", True, [])
    assert (visa["type"], visa["required"], visa["options"]) == ("select", True, ["Select...", "H-1B", "L-1"])
    assert set(why) >= {"key", "label", "type", "required", "options", "options_total", "autocomplete"}
    # they were left alone on the page
    assert not any(r["key"] in ctx.fields or r["key"] in ctx.written for r in ctx.unmapped_new_fields)


async def test_with_a_mapper_they_are_mapped_and_filled_and_nothing_is_left_unmapped(browser, pages) -> None:
    ctx = await mapped_ctx(browser, pages)
    seen: list[list[str]] = []

    async def mapper(new_fields: list[dict]) -> list[dict]:
        seen.append(sorted(f["label"] for f in new_fields))
        answers = {"Please explain": "Employer sponsored", "Visa type": "H-1B", "How often?": "Monthly"}
        return [{"key": f["key"], "label": f["label"], "field_type": f["type"], "source": "answer",
                 "value": answers[f["label"]], "basis": ["visa_details"], "generated": False} for f in new_fields]

    problems = await fill_form(browser, GENERIC, ctx, map_new_fields=mapper)
    assert problems == [] and ctx.unmapped_new_fields == []
    assert seen == [["How often?", "Please explain", "Visa type"]]  # every new field goes to the mapper, optional or not
    assert sorted(ctx.written.values()) == sorted(["Jane", "Yes", "Yes", "Employer sponsored", "H-1B", "Monthly"])


async def test_the_list_follows_the_page(browser, pages) -> None:
    """Recomputed from the page on every call: a field that went away is not asked about."""
    from yahbah.agent.fill import fill_new_fields

    ctx = await mapped_ctx(browser, pages)
    await fill_form(browser, GENERIC, ctx)
    assert len(ctx.unmapped_new_fields) == 2
    from yahbah.agent.tools import run_js
    await run_js(browser, "document.getElementById('sponsor-more').hidden = true; 1")
    assert await fill_new_fields(browser, ctx, None, lambda _: None) == 0
    assert ctx.unmapped_new_fields == []


async def test_a_revealed_field_with_an_approved_mapping_is_filled_not_asked_about(browser, pages) -> None:
    """The review re-fill: ctx.fields is the mapped form, ctx.mappings holds every approved answer, including those of
    fields that only appear once an earlier answer is given."""
    ctx = await mapped_ctx(browser, pages)
    await fill_form(browser, GENERIC, ctx)  # reveals them (unmapped), which gives their keys
    revealed = {r["label"]: r for r in ctx.unmapped_new_fields}
    ctx2 = await mapped_ctx(browser, pages)  # a fresh page and context: only the visible fields in ctx.fields
    for label, value in (("Please explain", "Employer sponsored"), ("Visa type", "H-1B")):
        key = revealed[label]["key"]
        ctx2.mappings[key] = {"key": key, "label": label, "field_type": revealed[label]["type"], "source": "answer",
                              "value": value, "basis": ["visa_details"], "generated": False}
    problems = await fill_form(browser, GENERIC, ctx2)
    assert problems == [] and ctx2.unmapped_new_fields == []
    assert {ctx2.fields[r["key"]]["label"] for r in revealed.values()} == {"Please explain", "Visa type"}


async def test_locked_answers_cannot_be_changed_by_the_fix_agent_tools(browser, pages, agent_llm) -> None:
    """Without a mapper (an approved re-fill) fill_field writes the approved answer or nothing, and problems_for
    reports a field whose last write came from elsewhere."""
    from yahbah.agent.fill import problems_for, read_values, write_field
    from yahbah.agent.tools import make_tools

    ctx = await mapped_ctx(browser, pages)
    ctx.locked = True
    sponsor = next(k for k, f in ctx.fields.items() if f["label"] == "Do you need sponsorship?")
    tools, _ = make_tools(fill_ctx=ctx)

    async def fill_field(key, value):
        return await tools.fill_field(key=key, value=value, browser_session=browser)

    assert (await fill_field(sponsor, "No")).error and "approved answer" in (await fill_field(sponsor, "No")).error
    assert (await fill_field("999", "x")).error  # not an approved field
    assert (await fill_field(sponsor, "Yes")).error is None  # the approved answer, or an option that means it
    assert ctx.written[sponsor] == "Yes"

    await write_field(browser, ctx, sponsor, "No")  # something else writes a different value (as a test bypass)
    problems = problems_for(ctx, await read_values(browser, list(ctx.fields)), [])
    assert any("not the approved answer" in p for p in problems)


async def test_content_added_to_a_field_with_no_approved_answer_is_a_problem_at_the_fill_and_the_gate(browser, pages) -> None:
    from harness.forms import Claim, submit
    from yahbah.agent.discover import StageFailed
    from yahbah.agent.tools import run_js

    await load(browser, pages.write("reveal.html", PAGE))
    ctx = context_for(await get_form_schema(browser), {"First name": "Jane", "Do you need sponsorship?": "No"})
    problems = await fill_form(browser, GENERIC, ctx)  # no mapper: locked; "Newsletter" has no approved answer
    assert problems == [] and ctx.locked and ctx.baseline
    # something other than our code chooses an option in the field nobody answered
    await run_js(browser, "document.getElementById('news').value = 'Yes'; 1")
    claim = Claim()
    with pytest.raises(StageFailed) as exc:
        await submit(browser, GENERIC, ctx, claim=claim)
    assert exc.value.reason == "fill_failed" and "Newsletter" in exc.value.detail and claim.calls == 0


# ── option misses: re-answered once with the options the page really offers ───

async def miss_ctx(browser, fixture_server):
    await load(browser, fixture_server.url("synthetic/option_miss.html"))
    return context_for(await get_form_schema(browser), {
        "First name": "Jane", "How did you hear?": "LinkedIn post", "Open to relocation?": "Maybe"})


def answer_rows(rows: list[dict], values: dict[str, str]) -> list[dict]:
    return [{"key": r["key"], "label": r["label"], "field_type": r["type"], "source": "answer",
             "value": values[r["label"]], "basis": ["f"], "generated": False} for r in rows]


def no_option_fits(llm) -> None:
    llm.on("driver", "_OptionPick", handler=lambda call: {"reasoning": "none fits", "choice": None})


async def test_a_planned_value_matching_no_option_is_re_answered_once_with_the_page_options(
        browser, fixture_server, llm) -> None:
    no_option_fits(llm)
    ctx = await miss_ctx(browser, fixture_server)
    seen: list[list[dict]] = []

    async def mapper(rows: list[dict]) -> list[dict]:
        seen.append(rows)
        return answer_rows(rows, {"How did you hear?": "Referral", "Open to relocation?": "Not sure"})

    problems = await fill_form(browser, GENERIC, ctx, map_new_fields=mapper)

    assert problems == [] and ctx.option_misses == {}
    assert len(seen) == 1  # once
    rows = {r["label"]: r for r in seen[0]}
    assert set(rows) == {"How did you hear?", "Open to relocation?"}  # First name filled; it is not asked again
    hear, relocate = rows["How did you hear?"], rows["Open to relocation?"]
    assert hear["options"] == ["Select...", "Job board", "Referral", "Other"] and hear["options_total"] is None
    assert relocate["options"] == ["Yes", "No", "Not sure"]
    assert (hear["type"], hear["required"], relocate["type"]) == ("select", True, "radio")
    assert hear["feedback"] == "'LinkedIn post' matches none of this field's options; choose one of them."
    assert "'Maybe'" in relocate["feedback"]
    assert set(hear) >= {"key", "label", "type", "required", "options", "options_total", "description", "section",
                         "autocomplete", "empty_value", "feedback"}
    assert hear["autocomplete"] is None
    by_label = {ctx.fields[k]["label"]: v for k, v in ctx.written.items()}
    assert by_label == {"First name": "Jane", "How did you hear?": "Referral", "Open to relocation?": "Not sure"}
    assert {ctx.mappings[r["key"]]["value"] for r in seen[0]} == {"Referral", "Not sure"}


async def test_a_second_miss_is_not_re_answered_and_stays_a_fill_problem(browser, fixture_server, llm, agent_llm) -> None:
    no_option_fits(llm)
    agent_llm.done(data={"fixed": False, "unresolved": ["How did you hear?", "Open to relocation?"]})
    ctx = await miss_ctx(browser, fixture_server)
    calls = 0

    async def mapper(rows: list[dict]) -> list[dict]:
        nonlocal calls
        calls += 1
        return answer_rows(rows, {"How did you hear?": "Podcast", "Open to relocation?": "Perhaps"})

    problems = await fill_form(browser, GENERIC, ctx, map_new_fields=mapper)

    assert calls == 1
    assert any("How did you hear?" in p for p in problems) and any("Open to relocation?" in p for p in problems)


async def test_without_a_mapper_an_option_miss_is_not_re_answered(browser, fixture_server, llm, agent_llm) -> None:
    no_option_fits(llm)
    agent_llm.done(data={"fixed": False, "unresolved": []})
    ctx = await miss_ctx(browser, fixture_server)
    problems = await fill_form(browser, GENERIC, ctx)  # locked: the approved answers are final
    assert any("How did you hear?" in p for p in problems)
    assert ctx.mappings[next(k for k, f in ctx.fields.items() if f["label"] == "How did you hear?")]["value"] == "LinkedIn post"
