"""The structural verdict on a submit (agent/submit.py) and the key-press parser of the agent guard
(agent/controls.py): pure functions, no browser. The browser behaviour is in test_submit_flow.py."""
import json

import pytest

from yahbah.agent.controls import Keys, parse_keys
from yahbah.agent.submit import (
    CONFIRMATION,
    NEGATIVE,
    SubmissionEvidence,
    PostSubmitState,
    advice_record,
    assess,
    find_markers,
)


def evidence(**kw) -> SubmissionEvidence:
    """A page that still shows the form we submitted: nothing decisive."""
    base = dict(url="https://x/apply", doc_replaced=False, controls=12, controls_before=12,
                cta_present=True, cta_visible=True, cta_disabled=False, errors=[], code_inputs=0,
                in_flight=False, confirmation=[], negative=[], excerpt="")
    return SubmissionEvidence(**{**base, **kw})


GONE = dict(controls=0, cta_present=False, cta_visible=False)


# ── assess ────────────────────────────────────────────────────────────────────

def test_the_form_still_there_with_no_errors_is_pending_whatever_the_page_says() -> None:
    assert assess(evidence()) == "pending"
    # a no-op click, a stale banner, an in-flight request
    assert assess(evidence(confirmation=["thank you for applying"])) == "pending"
    assert assess(evidence(in_flight=True, cta_disabled=True)) == "pending"
    assert assess(evidence(doc_replaced=True)) == "pending"  # a reload that brought the same form back


def test_errors_on_the_form_are_errors() -> None:
    assert assess(evidence(errors=[{"key": "3", "message": "Required"}])) == "errors"


def test_a_code_prompt_wins() -> None:
    assert assess(evidence(code_inputs=8)) == "code_prompt"
    assert assess(evidence(code_inputs=8, errors=[{"key": None, "message": "x"}])) == "code_prompt"


def test_form_and_control_gone_with_a_confirmation_is_submitted() -> None:
    assert assess(evidence(**GONE, confirmation=["application received"])) == "submitted"
    # it did not need the document to change: a single-page app swaps its content
    assert assess(evidence(**GONE, confirmation=["thank you for applying"], doc_replaced=False)) == "submitted"
    # a lingering spinner elsewhere on a confirmation page does not undo it
    assert assess(evidence(**GONE, confirmation=["thank you for applying"], in_flight=True)) == "submitted"


def test_form_and_control_gone_without_a_confirmation_is_only_probable() -> None:
    assert assess(evidence(**GONE)) == "probable"


def test_a_page_that_says_something_went_wrong_is_never_submitted() -> None:
    assert assess(evidence(**GONE, confirmation=["thank you for applying"], negative=["something went wrong"])) == "pending"
    assert assess(evidence(**GONE, negative=["something went wrong"])) == "pending"
    assert assess(evidence(**GONE, in_flight=True)) == "pending"  # a spinner replaced the form: wait


def test_fields_gone_but_the_submit_control_is_still_visible_is_pending() -> None:
    assert assess(evidence(controls=0, cta_visible=True, confirmation=["thank you for applying"])) == "pending"


@pytest.mark.parametrize("before, now, gone", [
    (12, 12, False), (12, 7, False), (12, 6, False), (12, 5, True), (12, 0, True),
    (3, 2, False), (3, 1, True), (1, 0, True), (1, 1, False),
    (0, 0, False),  # no baseline: nothing can be called gone
])
def test_the_form_is_gone_when_fewer_than_half_its_controls_are_left(before: int, now: int, gone: bool) -> None:
    assert evidence(controls=now, controls_before=before).form_gone is gone


def test_evidence_is_json_serializable() -> None:
    ev = evidence(errors=[{"key": "1", "message": "Required"}], confirmation=["a"], excerpt="Thanks")
    assert json.loads(json.dumps(ev.as_dict()))["errors"] == [{"key": "1", "message": "Required"}]
    assert ev.as_dict()["form_gone"] is False
    assert ev.as_dict()["submit_control"] == {"present": True, "visible": True, "disabled": False}


# ── markers ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Thank you for applying to Acme!",
    "Thanks for applying",
    "Your application has been received.",
    "Application submitted",
    "Your application was successfully submitted",
    "We have received your application and will be in touch",
    "We've received your application",
    "You have successfully applied",
    "Application confirmation",
])
def test_confirmation_phrases(text: str) -> None:
    assert find_markers(CONFIRMATION, text), text


@pytest.mark.parametrize("text", [
    "Submit application",
    "Apply for this job",
    "Fields marked * are required",
    "Your application has not been submitted",
    "Please review your application before you submit it",
    "Software Engineer at Acme",
    "Thank you for your interest in this role. We are a fast-growing team.",  # a job description's closing line
])
def test_not_confirmations(text: str) -> None:
    assert not find_markers(CONFIRMATION, text), text


@pytest.mark.parametrize("text", [
    "Something went wrong",
    "An error occurred while submitting",
    "Please try again later",
    "Your application has not been submitted",
    "Page not found",
])
def test_negative_phrases(text: str) -> None:
    assert find_markers(NEGATIVE, text), text


def test_markers_are_deduplicated_and_normalised() -> None:
    assert find_markers(CONFIRMATION, "Thank  you for applying. THANK YOU FOR APPLYING.") == ["thank you for applying"]


# ── the model's advice is data ────────────────────────────────────────────────

def test_advice_record_quotes_and_caps_what_the_page_made_the_model_say() -> None:
    advice = PostSubmitState(reasoning="x" * 1000, state="needs_fix", problems=["p" * 500] * 20)
    rec = advice_record(advice, vision=True)
    assert rec["vision"] is True and rec["state"] == "needs_fix"
    assert len(rec["problems"]) == 10 and all(len(p) <= 200 for p in rec["problems"])
    assert len(rec["reasoning"]) <= 300
    assert advice_record(None, False) is None


# ── key presses ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("keys, want", [
    ("Enter", Keys(enter=True, modified=False, space=False)),
    ("enter", Keys(True, False, False)),
    ("Return", Keys(True, False, False)),
    (" Enter ", Keys(True, False, False)),
    ("Shift+Enter", Keys(True, False, False)),
    ("Control+Enter", Keys(True, True, False)),
    ("cmd+Enter", Keys(True, True, False)),
    ("Alt+Control+Return", Keys(True, True, False)),
    ("Space", Keys(False, False, True)),
    (" ", Keys(False, False, True)),
    ("hello world", Keys(False, False, True)),       # typed text with a space activates a focused button
    ("hello\n", Keys(True, False, False)),            # a newline in typed text is Enter
    ("a\r b", Keys(True, False, True)),
    ("Escape", Keys(False, False, False)),
    ("ArrowDown", Keys(False, False, False)),
    ("Control+a", Keys(False, True, False)),
    ("Tab", Keys(False, False, False)),
    ("+", Keys(False, False, False)),
    ("1+Enter", Keys(False, False, False)),           # not a combination: typed as text
    ("", Keys(False, False, False)),
])
def test_parse_keys(keys: str, want: Keys) -> None:
    assert parse_keys(keys) == want
