"""Code-side decisions of the fill stage: option matching, read-back checks, problem lists, fill evidence rows.
(The submit stage's structural verdict is in test_submit_evidence_logic.py.)"""
import pytest

from yahbah.agent import fill
from yahbah.agent.fill import (
    FillContext, FillError, NoMatchingOption, fill_keys, intended_answer, problems_for, submitted_values, value_ok,
)
from yahbah.agent.options import match_option


# ── match_option ──────────────────────────────────────────────────────────────

def test_exact_match() -> None:
    assert match_option("Yes", ["Yes", "No"]) == "Yes"
    assert match_option("no", ["No", "Not applicable"]) == "No"


def test_whole_words_only() -> None:
    assert match_option("male", ["Female", "Male", "Decline to self-identify"]) == "Male"
    # "male" is inside "female" but not as a word: must not be picked
    assert match_option("male", ["Female", "Non-binary"]) is None


def test_prefix_either_way() -> None:
    assert match_option("United States of America", ["United States", "Canada"]) == "United States"
    assert match_option("Yes", ["Yes, I am authorized", "No, I am not"]) == "Yes, I am authorized"
    assert match_option("United States", ["United States", "United States Minor Outlying Islands"]) == "United States"


def test_word_inside_longer_option() -> None:
    assert match_option("LinkedIn", ["Job board (e.g. LinkedIn, Indeed)", "Referral"]) == "Job board (e.g. LinkedIn, Indeed)"


def test_prefixes_are_whole_words() -> None:
    """Audit LLM F1: "No" is a prefix of "Now" but not a word-prefix of it."""
    assert match_option("No", ["Now", "Later"]) is None
    assert match_option("No", ["Now", "No"]) == "No"
    assert match_option("No", ["Now", "No, thanks"]) == "No, thanks"
    assert match_option("Yes", ["Yesterday", "Maybe"]) is None
    assert match_option("Yes", ["Yesterday", "Yes, I do"]) == "Yes, I do"
    assert match_option("In", ["Indiana", "Illinois"]) is None
    assert match_option("US", ["USA", "Uruguay"]) is None
    # the other direction: the option is a word-prefix of the intended answer
    assert match_option("No thanks", ["No", "Now"]) == "No"
    assert match_option("Now or never", ["No", "Now"]) == "Now"
    # still prefixes by words
    assert match_option("United States of America", ["United States", "Canada"]) == "United States"


def test_ambiguous_or_empty_goes_to_the_model() -> None:
    assert match_option("Engineer", ["Software Engineer", "Hardware Engineer"]) is None
    assert match_option("", ["Yes"]) is None
    assert match_option("Remote", ["...", "Remote"]) == "Remote"  # truncation marker ignored


# ── value_ok ──────────────────────────────────────────────────────────────────

TEXT = {"type": "text"}


def combo(empty: str | None = None) -> dict:
    return {"type": "combobox", "empty_value": empty}


def test_value_ok_text() -> None:
    assert value_ok(TEXT, "Jane", {"value": " Jane "})
    assert not value_ok(TEXT, "Jane", {"value": "Jan"})
    assert not value_ok(TEXT, "Jane", {"missing": True, "value": ""})


def test_value_ok_custom_dropdown_without_snapshot() -> None:
    assert value_ok(combo(), "United States", {"value": "United States ×"})
    assert not value_ok(combo(), "United States", {"value": "Select..."})


def test_value_ok_custom_dropdown_against_its_empty_snapshot() -> None:
    """The three cases from the first live Greenhouse run (KONUX)."""
    # The phone-code picker shows only the dial code once "United States +1" is chosen
    assert value_ok(combo("Country* Select..."), "United States +1", {"value": "Country* +1"})
    # Still empty: the label's own "(Yes/No)" must not count as the chosen "Yes"
    assert not value_ok(combo("Visa Required (Yes/No)* Select..."), "Yes",
                        {"value": "Visa Required (Yes/No)* Select..."})
    assert value_ok(combo("Data privacy statement: * Select..."), "Yes",
                    {"value": "Data privacy statement: * Yes"})
    # Another option than the one we chose
    assert not value_ok(combo("Country* Select..."), "United States +1", {"value": "Country* +44"})
    # The chosen word already occurs in the label: words are counted, so the extra "No" counts
    assert value_ok(combo("Visa Required (Yes/No)* Select..."), "No", {"value": "Visa Required (Yes/No)* No"})


def test_a_verified_upload_is_not_reported_empty() -> None:
    """Live run 3: the widget swapped the input for the file name; the gate must not call it empty."""
    ctx = FillContext(fields={"6": {"label": "Resume/CV", "type": "file", "required": True, "options": []}},
                      mappings={}, files={})
    ctx.written = {"6": "resume.pdf"}
    assert problems_for(ctx, {"6": {"missing": True, "value": "", "shown": "resume.pdf Remove"}}, []) == []
    assert problems_for(ctx, {"6": {"missing": True, "value": "", "shown": "Attach"}}, []) != []


def test_value_ok_file_by_input_or_shown_name() -> None:
    file = {"type": "file"}
    assert value_ok(file, "resume.pdf", {"files": 1, "value": "resume.pdf"})
    # Upload widgets often clear the input and show the file name next to it instead
    assert value_ok(file, "resume.pdf", {"files": 0, "value": "", "shown": "resume.pdf Remove"})
    # ...or remove the input entirely: its tagged container still shows the file
    assert value_ok(file, "resume.pdf", {"missing": True, "value": "", "shown": "resume.pdf Remove"})
    assert not value_ok(file, "resume.pdf", {"files": 0, "value": "", "shown": "Attach Dropbox"})


def test_value_ok_checkbox() -> None:
    box = {"type": "checkbox"}
    assert value_ok(box, "checked", {"checked": True})
    assert not value_ok(box, "checked", {"checked": False})


# ── problems_for / FillContext ────────────────────────────────────────────────

def _ctx() -> FillContext:
    return FillContext(
        fields={
            "1": {"label": "First Name", "type": "text", "required": True, "options": []},
            "2": {"label": "Email", "type": "email", "required": True, "options": []},
            "3": {"label": "Website", "type": "url", "required": False, "options": []},
            "4": {"label": "Resume/CV", "type": "file", "required": True, "options": []},
        },
        mappings={
            # an old-history source (before the answer engine): still filled with its value (C2 replay safety)
            "1": {"key": "1", "source": "first_name", "value": "Jane", "confidence": 1.0},
            "4": {"key": "4", "source": "resume", "value": ""},
            "5": {"key": "5", "source": "cover_letter", "value": ""},
        },
        files={"resume": "/tmp/resume.pdf"},
        cover_letter_text="Dear team",
    )


def test_value_for_resolves_documents_at_fill_time() -> None:
    ctx = _ctx()
    assert ctx.value_for("1") == "Jane"
    assert ctx.value_for("4") == "/tmp/resume.pdf"  # file field -> path
    assert ctx.value_for("5") == "Dear team"  # cover letter into a text box -> the text
    assert ctx.value_for("3") is None  # unmapped


def test_problems_list_mismatch_empty_required_and_errors_but_not_empty_optional() -> None:
    ctx = _ctx()
    ctx.written = {"1": "Jane", "4": "resume.pdf"}
    values = {
        "1": {"value": "Jan"},                        # mismatch
        "2": {"value": ""},                           # required, empty
        "3": {"value": ""},                           # optional, empty: fine
        "4": {"value": "", "files": 0, "shown": ""},  # upload didn't stick
    }
    problems = problems_for(ctx, values, [{"key": "2", "message": "Email is required"}])
    assert any("First Name" in p and "'Jane'" in p for p in problems)
    assert any("Email" in p and "required but empty" in p for p in problems)
    assert not any("Website" in p for p in problems)
    assert any("Resume/CV" in p for p in problems)
    assert any("validation error" in p and "Email is required" in p for p in problems)


def test_no_problems_when_everything_matches() -> None:
    ctx = _ctx()
    ctx.written = {"1": "Jane", "2": "jane@example.com", "4": "resume.pdf"}
    values = {"1": {"value": "Jane"}, "2": {"value": "jane@example.com"}, "3": {"value": ""},
              "4": {"value": "resume.pdf", "files": 1}}
    assert problems_for(ctx, values, []) == []


def test_a_field_without_a_plan_must_keep_its_baseline_content_in_every_mode() -> None:
    from yahbah.agent.fill import content_signature

    for locked in (False, True):  # a first fill (mapper) as well as an approved re-fill
        ctx = _ctx()
        ctx.locked = locked
        ctx.baseline = {"3": content_signature({"value": "https://site-prefilled.test"})}
        def baseline_problems(value: str) -> list[str]:
            return [p for p in problems_for(ctx, {"3": {"value": value}}, []) if "no planned answer" in p]

        assert baseline_problems("https://site-prefilled.test") == []
        (changed,) = baseline_problems("https://site-prefilled.test/x")
        assert "Website" in changed


# ── fill evidence rows (contract run-lifecycle §8) ────────────────────────────

def test_submitted_values_rows_have_the_contract_shape() -> None:
    ctx = _ctx()
    ctx.fields["2"]["options"] = ["a", "b"]
    ctx.mappings["2"] = {"key": "2", "source": "email", "value": "jane@example.com", "basis": ["email"],
                         "generated": False}
    ctx.mappings["3"] = {"key": "3", "source": "custom", "value": "I build things.", "basis": ["experience"],
                         "generated": True}
    ctx.written = {"1": "Jane", "2": "jane@example.com", "4": "resume.pdf"}
    values = {"1": {"value": "Jane"}, "2": {"value": "jane@example.com"}, "3": {"value": ""},
              "4": {"value": "", "missing": True, "files": 0, "shown": "resume.pdf Remove"}}
    rows = {r["key"]: r for r in submitted_values(ctx, values)}

    assert set(rows["1"]) == {"key", "label", "type", "required", "options", "answer", "source", "basis",
                              "generated", "written", "page_value", "ok"}
    assert rows["1"] | {"basis": [], "generated": False} == {
        "key": "1", "label": "First Name", "type": "text", "required": True, "options": [], "answer": "Jane",
        "source": "first_name", "basis": [], "generated": False, "written": "Jane", "page_value": "Jane", "ok": True}
    # basis / generated come through from the mapping
    assert rows["2"]["basis"] == ["email"] and rows["2"]["options"] == ["a", "b"]
    assert rows["3"]["generated"] is True and rows["3"]["basis"] == ["experience"]
    assert rows["3"]["written"] is None and rows["3"]["ok"] is True  # nothing written: nothing to disagree
    # a document is shown by its file name, never its path; a swapped-out upload widget shows the name it shows
    assert rows["4"]["answer"] == "resume.pdf" and rows["4"]["page_value"] == "resume.pdf Remove"
    assert intended_answer(ctx, "4") == "resume.pdf"
    # an unmapped field has no answer, source or basis
    row = {"key": "9", "label": "Other", "type": "text", "required": False, "options": [], "answer": None,
           "source": None, "basis": [], "generated": False, "written": None, "page_value": None, "ok": True}
    ctx.fields["9"] = {"label": "Other", "type": "text", "required": False, "options": []}
    assert {r["key"]: r for r in submitted_values(ctx, {})}["9"] == row


def test_submitted_values_marks_a_disagreement() -> None:
    ctx = _ctx()
    ctx.written = {"1": "Jane"}
    (row,) = [r for r in submitted_values(ctx, {"1": {"value": "Jan"}}) if r["key"] == "1"]
    assert row["ok"] is False and row["page_value"] == "Jan" and row["written"] == "Jane"


# ── text_allowed (contract C8) ────────────────────────────────────────────────

@pytest.mark.parametrize("text, allowed", [
    ("", True), ("   ", True),                    # nothing typed
    ("Jane", True), ("jane", True), ("Ja", True),  # (part of) a planned answer, normalized
    ("dear team", True),                          # the cover letter text planned for a text box
    ("resume.pdf", True),                         # a planned document path
    ("Janet", False), ("Blue", False), ("Jane and Bob", False),
])
def test_text_allowed_is_only_part_of_a_planned_answer(text, allowed) -> None:
    assert _ctx().text_allowed(text) is allowed


def test_text_allowed_ignores_skipped_and_unplanned_fields() -> None:
    ctx = _ctx()
    ctx.mappings["3"] = {"key": "3", "source": "skip", "value": "Hidden"}
    assert not ctx.text_allowed("Hidden")


# ── the fix agent writes planned values only, in every mode ───────────────────

class _Registry:
    """Stands in for browser-use's Tools: keeps the registered fill tools as plain coroutines."""

    def __init__(self) -> None:
        self.actions: dict = {}

    def action(self, _description):
        def register(fn):
            self.actions[fn.__name__] = fn
            return fn
        return register


def _fill_tools(ctx: FillContext) -> _Registry:
    registry = _Registry()
    fill.register_fill_tools(registry, ctx)
    return registry


def _fill_field_tool(ctx: FillContext, monkeypatch):
    writes: list[tuple[str, str]] = []

    async def write_field(session, ctx_, key, value):
        writes.append((key, value))
        return value

    async def read_values(session, keys):
        return {k: {"value": dict(writes)[k]} for k in keys}

    monkeypatch.setattr(fill, "write_field", write_field)
    monkeypatch.setattr(fill, "read_values", read_values)
    fill_field = _fill_tools(ctx).actions["fill_field"]

    async def call(key, value):
        return await fill_field(key=key, value=value, browser_session=None)
    return call, writes


@pytest.mark.parametrize("locked", [False, True])
async def test_fill_field_rejects_a_value_other_than_the_plan_in_every_mode(monkeypatch, locked) -> None:
    ctx = _ctx()
    ctx.locked = locked
    call, writes = _fill_field_tool(ctx, monkeypatch)
    result = await call("1", "Janet")
    assert result.error and "approved answer" in result.error and writes == []


@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("key", ["2", "3", "999"])  # no mapping / not on the form
async def test_fill_field_rejects_a_key_without_a_plan_in_every_mode(monkeypatch, locked, key) -> None:
    ctx = _ctx()
    ctx.locked = locked
    call, writes = _fill_field_tool(ctx, monkeypatch)
    result = await call(key, "anything")
    assert result.error and "no planned answer" in result.error and writes == []


@pytest.mark.parametrize("locked", [False, True])
async def test_fill_field_writes_the_planned_value_for_a_value_that_means_it(monkeypatch, locked) -> None:
    ctx = _ctx()
    ctx.locked = locked
    call, writes = _fill_field_tool(ctx, monkeypatch)
    assert (await call("1", " jane ")).error is None
    assert writes == [("1", "Jane")]


async def test_a_file_field_is_not_written_by_fill_field_and_upload_needs_the_planned_document(monkeypatch) -> None:
    ctx = _ctx()
    call, writes = _fill_field_tool(ctx, monkeypatch)
    assert (await call("4", "/etc/passwd")).error and writes == []
    upload_document = _fill_tools(ctx).actions["upload_document"]
    for key, which in (("1", "resume"), ("4", "cover_letter"), ("999", "resume")):
        result = await upload_document(key=key, which=which, browser_session=None)
        assert result.error and "leave it alone" in result.error  # unlocked too


# ── NoMatchingOption ──────────────────────────────────────────────────────────

def test_no_matching_option_keeps_the_options_the_page_offered() -> None:
    exc = fill._no_option("nothing fits", ["", "...", "Yes", "No"])
    assert isinstance(exc, NoMatchingOption) and isinstance(exc, FillError) and exc.options == ["Yes", "No"]


def test_no_options_at_all_is_a_plain_fill_error() -> None:
    exc = fill._no_option("nothing fits", ["", "..."])
    assert type(exc) is FillError  # a browser problem (menu never rendered), nothing to re-answer against


async def test_fill_keys_records_an_option_miss_and_keeps_the_field_a_problem(monkeypatch) -> None:
    ctx = _ctx()
    ctx.mappings["2"] = {"key": "2", "source": "answer", "value": "Maybe"}

    async def write_field(session, ctx_, key, value):
        if key == "2":
            raise NoMatchingOption("no option fits", ["Yes", "No"])
        raise FillError("could not focus")

    monkeypatch.setattr(fill, "write_field", write_field)
    await fill_keys(None, ctx, ["1", "2"], lambda _: None)
    assert ctx.option_misses == {"2": ("Maybe", ["Yes", "No"])}  # a plain FillError is not a miss
    assert ctx.written == {"1": "Jane", "2": "Maybe"}  # both stay in the problem list
