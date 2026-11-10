"""
Deterministic checks on a proposed answer, run before the verifier model sees it. Pure: no model,
no browser, no I/O.

`check_answer` returns the value (possibly repaired: a composed answer loses the sentences that state
numbers no fact contains) and the problems found, in rule order:

1. an empty value                                         -> invalid_value
2. evidence not shown for this field, or not in the catalog -> cites_unshown
3. a composed answer to a field that isn't free text      -> uncited
4. a non-composed answer citing nothing                   -> uncited
5. a value the widget can't take (option, yes/no, date, number, email, tel, url format) -> invalid_value
6. a date or month not found verbatim in a cited fact     -> unsupported
7. numbers: composed answers drop sentences with numbers no fact (nor the question) states
   (invalid_value if nothing is left); non-composed answers stating numbers their cited facts
   don't contain                                          -> unsupported
8. a quote not found (whole words, norm() forms) in a cited fact: a profile record's value, a stated
   fact's value or description (a boolean's value is only true/false) -> unsupported
9. an absence (`absent`) resting on profile records alone -> unsupported
10. a non-composed answer citing only profile records that gives neither quotes nor an absence -> uncited
11. a composed answer to a free-text field whose Pass 1 intent doesn't start with "open-ended:"
    (the field asks for a fact, so its answer must state facts)  -> invalid_value

Rules 8-10 are bookkeeping only: whether the quoted words or the stated absence actually answer the
question (another name for the same thing, a list declared complete) is the verifier's judgment.
"""
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from yahbah.agent.options import FALSY, TRUTHY, match_option, norm
from yahbah.llm.facts import FactCatalog, value_text
from yahbah.llm.grounding import drop_sentences, unsupported_numbers
from yahbah.schemas import FormField

__all__ = ["FREE_TEXT_TYPES", "PROFILE_RECORD_KINDS", "Problem", "check_answer", "is_free_text", "options_complete"]

FREE_TEXT_TYPES = frozenset({"text", "textarea"})
_CHOICE_TYPES = frozenset({"select", "combobox", "radio"})
# Profile facts that list what the applicant has (skills, bio, work history, education). Their silence
# about something is no evidence that the applicant lacks it.
PROFILE_RECORD_KINDS = frozenset({"skills", "bio", "work_experience", "education"})
# How Pass 1 (prompt answer_interpret) marks a question that wants the applicant's own words: only those get prose.
OPEN_ENDED = "open-ended:"

_FORMATS: dict[str, tuple[re.Pattern, str]] = {
    "date": (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "a date must be YYYY-MM-DD"),
    "month": (re.compile(r"^\d{4}-\d{2}$"), "a month must be YYYY-MM"),
    "time": (re.compile(r"^\d{2}:\d{2}$"), "a time must be HH:MM"),
    "number": (re.compile(r"^-?\d+(\.\d+)?$"), "a number field takes digits only"),
    "email": (re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"), "not an email address"),
    "tel": (re.compile(r"^\+?[\d\s().-]{6,}$"), "not a phone number"),
    "url": (re.compile(r"^https?://\S+$"), "not an http(s) URL"),
}


def is_free_text(f: FormField) -> bool:
    return f.field_type in FREE_TEXT_TYPES and not f.options


def options_complete(f: FormField) -> bool:
    return bool(f.options) and (f.options_total is None or f.options_total <= len(f.options))


def _is_choice(f: FormField) -> bool:
    return f.field_type in _CHOICE_TYPES or (f.field_type == "checkbox" and bool(f.options))


@dataclass
class Problem:
    code: Literal["uncited", "cites_unshown", "invalid_value", "unsupported"]
    message: str  # one sentence about the answer; never a fact value


def _widget_problem(f: FormField, value: str) -> str | None:
    if _is_choice(f):
        if options_complete(f) and match_option(value, f.options) is None:
            return "the answer is none of the field's options"
        return None  # options not all known: fill matches the value at write time
    if f.field_type == "checkbox":
        return None if norm(value) in TRUTHY | FALSY else "a single checkbox takes yes or no"
    if f.field_type not in _FORMATS:
        return None
    pattern, message = _FORMATS[f.field_type]
    if not pattern.match(value):
        return message
    if f.field_type == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return "not a real calendar date"
    return None


def _is_profile_record(catalog: FactCatalog, fact_id: str) -> bool:
    fact = catalog.facts[fact_id]
    return fact.source == "profile" and fact.kind in PROFILE_RECORD_KINDS


def _quotable(catalog: FactCatalog, fact_id: str) -> list[str]:
    """The words a quote may come from: a profile record's value; a stated fact's value and description."""
    fact = catalog.facts[fact_id]
    return [value_text(fact.value)] if _is_profile_record(catalog, fact_id) else [value_text(fact.value),
                                                                                  fact.description]


def _quoted(quote: str, texts: list[str]) -> bool:
    pattern = re.compile(rf"\b{re.escape(norm(quote))}\b")
    return any(pattern.search(norm(t)) for t in texts)


def check_answer(f: FormField, value: str, composed: bool, evidence: list[str], shown: set[str],
                 catalog: FactCatalog, *, intent: str, quotes: Iterable[str] = (),
                 absent: str | None = None) -> tuple[str, list[Problem]]:
    """(value, possibly repaired; problems). No problems means the answer may go to the verifier.
    `intent`: Pass 1's reading of the field; `quotes`: words of the cited facts the answer rests on;
    `absent`: what it rests on not finding."""
    value = value.strip()
    if not value:
        return value, [Problem("invalid_value", "the answer is empty")]
    problems: list[Problem] = []
    if [e for e in evidence if e not in shown or e not in catalog.facts]:
        problems.append(Problem("cites_unshown", "the answer cites facts that weren't shown for this question"))
    if composed and not is_free_text(f):
        problems.append(Problem("uncited", f"a {f.field_type} answer must state stored facts, not composed text"))
    if not composed and not evidence:
        problems.append(Problem("uncited", "the answer cites no stored fact"))
    if message := _widget_problem(f, value):
        problems.append(Problem("invalid_value", message))

    cited = [e for e in evidence if e in catalog.facts]
    if f.field_type in ("date", "month") and not any(value in v for v in catalog.values(cited)):
        problems.append(Problem("unsupported", "a date must come from a stored date"))

    question = [f.label, f.description, *f.options]
    if composed:
        if extra := unsupported_numbers(value, *question, *catalog.values()):
            value = drop_sentences(value, extra)
            if not value:
                problems.append(Problem("invalid_value", "every sentence stated numbers no stored fact contains"))
    elif unsupported_numbers(value, *question, *catalog.values(cited)):
        problems.append(Problem("unsupported", "the answer states numbers its cited facts don't contain"))

    quotes = [q for q in quotes if norm(q)]
    absent = absent if absent and norm(absent) else None
    cited_texts = [t for e in cited for t in _quotable(catalog, e)]
    if any(not _quoted(q, cited_texts) for q in quotes):
        problems.append(Problem("unsupported", "a quoted phrase isn't in the cited facts; copy it exactly as "
                                               "written there"))
    records_only = all(_is_profile_record(catalog, e) for e in cited)
    if absent and records_only:
        problems.append(Problem("unsupported", "an answer resting on something missing from the profile needs a "
                                               "stated fact that says so or that the list is complete"))
    if not composed and cited and records_only and not quotes and not absent:
        problems.append(Problem("uncited", "say which words in the profile the answer rests on (quotes) or what "
                                           "was not found (absent)"))
    if composed and is_free_text(f) and not intent.strip().lower().startswith(OPEN_ENDED):
        problems.append(Problem("invalid_value", "only an open-ended question gets a composed answer; answer "
                                                 "this field from its facts"))
    return value, problems
