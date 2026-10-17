"""Code-side decisions of the fill and submit stages: option matching, read-back checks,
problem lists, post-submit verdict consistency."""
from yahbah.agent.fill import FillContext, match_option, problems_for, value_ok
from yahbah.agent.submit import PostSubmitState, Signals, contradicts


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


# ── post-submit verdicts ──────────────────────────────────────────────────────

def _signals(**kw) -> Signals:
    base = dict(url="https://x/confirmation", form_fields_present=0, submit_button_visible=False,
                errors=[], code_inputs=0)
    return Signals(**{**base, **kw})


def test_verdict_consistency() -> None:
    assert PostSubmitState(reasoning="", state="submitted").consistent()
    assert not PostSubmitState(reasoning="", state="needs_fix").consistent()  # no problems listed
    assert PostSubmitState(reasoning="", state="needs_fix", problems=["Phone is invalid"]).consistent()
    assert not PostSubmitState(reasoning="", state="blocked").consistent()  # no reason
    assert PostSubmitState(reasoning="", state="blocked", blocked_reason="captcha").consistent()


def test_verdicts_that_contradict_the_page_get_a_second_opinion() -> None:
    submitted = PostSubmitState(reasoning="", state="submitted")
    assert not contradicts(submitted, _signals())
    assert contradicts(submitted, _signals(form_fields_present=9, errors=[{"key": "2", "message": "required"}]))
    assert contradicts(submitted, _signals(form_fields_present=9, code_inputs=8))
    assert contradicts(PostSubmitState(reasoning="", state="needs_fix", problems=["x"]), _signals())
    assert contradicts(PostSubmitState(reasoning="", state="email_code"), _signals(form_fields_present=9))
    assert not contradicts(PostSubmitState(reasoning="", state="email_code"), _signals(code_inputs=8))
