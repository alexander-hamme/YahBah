"""Code-side decisions of the fill stage: option matching, read-back checks, problem lists, fill evidence rows.
(The submit stage's structural verdict is in test_submit_evidence_logic.py.)"""
from yahbah.agent.fill import FillContext, intended_answer, match_option, problems_for, submitted_values, value_ok


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
            "1": {"key": "1", "source": "first_name", "value": "Jane"},
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
