"""Regression checks against saved job-board pages (test/yahbah/fixtures), offline.

Each case in cases.json is a page verified by hand: the board must be recognised from the DOM alone
(the page is served from 127.0.0.1 by the harness fixture server, so its URL says nothing about the
board) and read_form_schema must return exactly the expected fields. The fill plumbing (CDP typing,
read-back, file upload, submit guard) is exercised on the same pages.

Add a case whenever a page has been verified by hand (scripts/save_fixture.py saves one).
"""
import json
from pathlib import Path

import pytest

from harness.browser import load
from yahbah.agent.boards import detect_board
from yahbah.agent.fill import (
    GUARD_SUBMIT_JS,
    FillContext,
    capture_empty_values,
    fill_text,
    problems_for,
    read_values,
    upload_file,
)
from yahbah.agent.tools import get_form_schema, run_js

pytestmark = pytest.mark.browser

FIXTURES = Path(__file__).parents[1] / "fixtures"
CASES = json.loads((FIXTURES / "cases.json").read_text())


@pytest.fixture
def session(browser):
    return browser


@pytest.fixture
def load_fixture(fixture_server):
    async def load_fixture(session, filename: str) -> str:
        url = fixture_server.url(filename)
        await load(session, url)
        return url
    return load_fixture


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
async def test_board_and_fields(session, load_fixture, case) -> None:
    url = await load_fixture(session, case["file"])
    board = await detect_board(lambda c: run_js(session, c), url)
    assert board.name == case["board"]

    got = [(f["label"], f["type"], f["required"]) for f in await get_form_schema(session)]
    want = [(f["label"], f["type"], f["required"]) for f in case["fields"]]
    assert got == want


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
async def test_fill_plumbing(session, load_fixture, case, tmp_path) -> None:
    await load_fixture(session, case["file"])
    fields = await get_form_schema(session)
    await capture_empty_values(session, fields)  # as map_form does, before anything is filled
    by_label = {f["label"]: f for f in fields}
    text_field = next(f for f in fields if f["type"] in ("text", "email") and f["required"])
    file_field = next((f for f in fields if f["type"] == "file"), None)

    # Typing via CDP lands in the field and reads back
    assert await fill_text(session, text_field["key"], "Jane") == "Jane"
    values = await read_values(session, [text_field["key"]])
    assert values[text_field["key"]]["value"] == "Jane"

    # Typing replaces, never appends
    await fill_text(session, text_field["key"], "Janet")
    assert (await read_values(session, [text_field["key"]]))[text_field["key"]]["value"] == "Janet"

    # Files are set on the (hidden) <input type=file> directly
    if file_field:
        resume = tmp_path / "resume.pdf"
        resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
        assert await upload_file(session, file_field["key"], str(resume)) == "resume.pdf"
        assert (await read_values(session, [file_field["key"]]))[file_field["key"]]["files"] == 1

    # The submit gate sees every required field we haven't filled
    ctx = FillContext(
        fields={f["key"]: {"label": f["label"], "type": f["type"], "required": f["required"], "options": [],
                           "empty_value": f.get("empty_value")}
                for f in fields},
        mappings={}, files={},
    )
    ctx.written = {text_field["key"]: "Janet"}
    problems = problems_for(ctx, await read_values(session, list(ctx.fields)), [])
    empty_required = [lbl for lbl, f in by_label.items() if f["required"] and f["key"] != text_field["key"]
                      and f["key"] != (file_field or {}).get("key")]
    for label in empty_required:
        assert any(label in p for p in problems), f"gate missed empty required field {label!r}"


async def test_submit_guard_blocks_and_releases(session, load_fixture) -> None:
    await load_fixture(session, CASES[0]["file"])
    count_submits = """(() => {
        window.__submits = 0;
        document.addEventListener('submit', (e) => { e.preventDefault(); window.__submits++; });
        return true; })()"""
    await run_js(session, count_submits)
    # Fire the event directly: requestSubmit() would stop at HTML validation (required fields are empty)
    submit_form = """(() => {
        document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        return window.__submits; })()"""
    button_disabled = "document.querySelector('form button[type=submit]').disabled"

    await run_js(session, f"{GUARD_SUBMIT_JS}(true)")
    assert await run_js(session, submit_form) == 0  # cancelled before the page's handlers see it
    assert await run_js(session, button_disabled) is True
    await run_js(session, f"{GUARD_SUBMIT_JS}(false)")
    assert await run_js(session, submit_form) == 1  # released
    assert await run_js(session, button_disabled) is False
