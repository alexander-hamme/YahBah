"""The deterministic answer checks (yahbah.llm.answer_checks, contract C7): one test per rule, each
with its boundary case."""
import pytest

from yahbah.llm.answer_checks import check_answer, is_free_text, options_complete
from yahbah.llm.facts import Fact, FactCatalog
from yahbah.schemas import FormField

CATALOG = FactCatalog(facts={f.id: f for f in [
    Fact(id="years_python", kind="skill_experience_years", data_type="number", value=6, description="d"),
    Fact(id="start", kind="start_date", data_type="date", value="2026-01-15", description="d"),
    Fact(id="notice", kind="availability", data_type="relative_availability", value="2 weeks after an offer",
         description="d"),
    Fact(id="auth", kind="work_authorization", data_type="boolean", value=True, description="d"),
    Fact(id="sponsorship", kind="visa_sponsorship", data_type="boolean", value=False,
         description="The applicant will need visa sponsorship to work in the US"),
    Fact(id="work", kind="work_experience", value="Built a Kafka service handling 2B events/day.", description="d"),
    # Profile records (source profile; skills / bio / work_experience / education) and a stated fact about them.
    Fact(id="profile.skills", kind="skills", data_type="list", value=["Python", "Kafka", "PostgreSQL"],
         description="Technologies and skills the applicant has professional experience with", source="profile"),
    Fact(id="profile.work_experience[0]", kind="work_experience", value="Engineer at Acme Data. Built Kafka pipelines.",
         description="d", source="profile"),
    Fact(id="history_complete", kind="employment_history_complete", data_type="boolean", value=True, description="d"),
]}, placeholders=())
ALL = set(CATALOG.facts)
SKILLS, WORK = "profile.skills", "profile.work_experience[0]"
OPEN = "open-ended: describing their work"  # a Pass 1 intent that allows a composed answer (rule 11)


def field(ftype: str = "text", label: str = "Question", options=None, options_total=None, description="") -> FormField:
    return FormField(key="1", label=label, field_type=ftype, options=options or [], options_total=options_total,
                     description=description)


def codes(f: FormField, value: str, composed: bool = False, evidence=("auth",), shown=ALL, quotes=(),
          absent=None, intent: str = OPEN) -> list[str]:
    return [p.code for p in check_answer(f, value, composed, list(evidence), set(shown), CATALOG,
                                         quotes=list(quotes), absent=absent, intent=intent)[1]]


def messages(f: FormField, value: str, evidence, quotes=(), absent=None, composed=False, intent=OPEN) -> list[str]:
    return [p.message for p in check_answer(f, value, composed, list(evidence), ALL, CATALOG, quotes=list(quotes),
                                            absent=absent, intent=intent)[1]]


def test_free_text_and_complete_options() -> None:
    assert is_free_text(field("text")) and is_free_text(field("textarea"))
    assert not is_free_text(field("text", options=["a"])) and not is_free_text(field("email"))
    assert options_complete(field("select", options=["a", "b"])) and options_complete(field("select", options=["a"],
                                                                                          options_total=1))
    assert not options_complete(field("select", options=["a"], options_total=2)) and not options_complete(field("select"))


def test_rule1_an_empty_value_is_invalid_and_nothing_else_is_checked() -> None:
    assert codes(field(), "  ", evidence=()) == ["invalid_value"]


def test_rule2_evidence_must_be_shown_and_in_the_catalog() -> None:
    assert codes(field("radio", options=["Yes", "No"]), "Yes", shown={"auth"}) == []
    assert codes(field("radio", options=["Yes", "No"]), "Yes", shown={"start"}) == ["cites_unshown"]
    assert codes(field("radio", options=["Yes", "No"]), "Yes", evidence=("ghost",), shown={"ghost"}) == ["cites_unshown"]


def test_rule3_composed_text_only_on_free_text_fields() -> None:
    assert codes(field("textarea"), "I enjoy hard problems.", composed=True, evidence=()) == []
    assert codes(field("radio", options=["Yes", "No"]), "Yes", composed=True) == ["uncited"]
    assert codes(field("text", options=["Yes", "No"]), "Yes", composed=True) == ["uncited"]


def test_rule4_a_stated_fact_must_cite_one() -> None:
    assert codes(field(), "Yes", evidence=()) == ["uncited"]


def test_rule5_choice_values_must_match_a_known_option() -> None:
    assert codes(field("select", options=["Yes", "No"]), "yes") == []
    assert codes(field("radio", options=["Now", "Later"]), "No") == ["invalid_value"]  # "No" is not "Now"
    assert codes(field("checkbox", options=["Python", "Go"]), "Rust") == ["invalid_value"]
    assert codes(field("combobox", options=["Yes", "No"], options_total=40), "Maybe") == []  # fill decides
    assert codes(field("select"), "Anything") == []  # no options read: fill matches at write time


@pytest.mark.parametrize("value,ok", [("yes", True), ("Checked", True), ("decline", True), ("maybe", False)])
def test_rule5_a_single_checkbox_takes_yes_or_no(value, ok) -> None:
    assert codes(field("checkbox"), value) == ([] if ok else ["invalid_value"])


@pytest.mark.parametrize("ftype,value,ok", [
    ("date", "2026-01-15", True), ("date", "2026-02-30", False), ("date", "2026-1-15", False),
    ("month", "2026-01", True), ("month", "2026-1", False),
    ("time", "09:30", True), ("time", "9:30", False),
    ("number", "6", True), ("number", "6 years", False), ("number", "-6.5", True),
    ("email", "jane@example.test", True), ("email", "jane at example", False),
    ("tel", "+1 (617) 010-0100", True), ("tel", "12345", False),
    ("url", "https://x.test/a", True), ("url", "x.test/a", False),
])
def test_rule5_formats(ftype, value, ok) -> None:
    problems = [p for p in check_answer(field(ftype), value, False, ["start", "years_python"], ALL, CATALOG,
                                        intent=OPEN)[1]
                if p.code == "invalid_value"]
    assert (not problems) is ok


def test_rule6_a_date_must_come_from_a_stored_date() -> None:
    assert codes(field("date"), "2026-01-15", evidence=("start",)) == []
    assert codes(field("month"), "2026-01", evidence=("start",)) == []
    problems = check_answer(field("date"), "2026-02-01", False, ["notice"], ALL, CATALOG, intent=OPEN)[1]
    assert [p.message for p in problems][0] == "a date must come from a stored date"  # never from "2 weeks"


def test_rule7_composed_answers_lose_sentences_with_invented_numbers() -> None:
    f = field("textarea", label="Tell us about a project")
    value, problems = check_answer(f, "I built a Kafka service handling 2B events/day. It cut costs by 40%.",
                                   True, ["work"], {"work"}, CATALOG, intent=OPEN)
    assert (value, problems) == ("I built a Kafka service handling 2B events/day.", [])
    # Any fact's numbers count for composed prose, not only the cited ones.
    assert check_answer(f, "I have 6 years of Python.", True, [], set(), CATALOG, intent=OPEN) == (
        "I have 6 years of Python.", [])
    value, problems = check_answer(f, "It cut costs by 40%.", True, ["work"], {"work"}, CATALOG, intent=OPEN)
    assert value == "" and [p.code for p in problems] == ["invalid_value"]


def test_rule7_stated_numbers_must_be_in_the_cited_facts_or_the_question() -> None:
    assert codes(field(), "6", evidence=("years_python",)) == []
    assert codes(field(), "7", evidence=("years_python",)) == ["unsupported"]
    assert codes(field(), "6", evidence=("auth",)) == ["unsupported"]  # 6 is a fact, but not a cited one
    assert codes(field(label="More than 5 years?"), "Yes, more than 5", evidence=("years_python",)) == []


QUOTE_MISS = "a quoted phrase isn't in the cited facts; copy it exactly as written there"
ABSENCE_ON_RECORDS = ("an answer resting on something missing from the profile needs a stated fact that says so "
                      "or that the list is complete")
SAY_WHAT = "say which words in the profile the answer rests on (quotes) or what was not found (absent)"


def yes_no(label: str) -> FormField:
    return field("radio", label=label, options=["Yes", "No"])


def test_rule8_a_quote_must_be_in_a_cited_fact_whatever_the_polarity() -> None:
    lack = yes_no("Do you lack PostgreSQL experience?")
    assert codes(lack, "No", evidence=(SKILLS,), quotes=["PostgreSQL"]) == []
    assert codes(lack, "No", evidence=(SKILLS,), quotes=["postgresql"]) == []  # norm(): case and punctuation
    # Another name for the same thing is the verifier's call; code only checks the words are there.
    assert messages(lack, "No", (SKILLS,), quotes=["Postgres"]) == [QUOTE_MISS]
    assert codes(lack, "No", evidence=(SKILLS,), quotes=["SQL"]) == ["unsupported"]  # whole words only
    assert codes(yes_no("Kafka?"), "Yes", evidence=(WORK,), quotes=["Kafka pipelines"]) == []  # a phrase


def test_rule8_quotes_count_only_against_cited_facts_and_every_quote_is_checked() -> None:
    q = yes_no("Do you know PostgreSQL?")
    assert codes(q, "Yes", evidence=("auth",), quotes=["PostgreSQL"]) == ["unsupported"]  # in the catalog, not cited
    assert codes(q, "Yes", evidence=(SKILLS, "auth"), quotes=["PostgreSQL"]) == []  # mixed evidence
    assert codes(q, "Yes", evidence=(SKILLS, "auth"), quotes=["PostgreSQL", "Rust"]) == ["unsupported"]


def test_rule8_a_stated_fact_is_quoted_from_its_value_or_description_a_profile_record_from_its_value() -> None:
    # A boolean's value is true/false: what it says is in its description.
    sponsorship = yes_no("Will you now or in the future require visa sponsorship?")
    assert codes(sponsorship, "No", evidence=("sponsorship",), quotes=["need visa sponsorship"]) == []
    assert codes(sponsorship, "No", evidence=("sponsorship",), quotes=["sponsorship is not needed"]) == ["unsupported"]
    # A profile record's words are its value: the skills description names no skill.
    assert messages(yes_no("Professional experience with Rust?"), "Yes", (SKILLS,),
                    quotes=["professional experience"]) == [QUOTE_MISS]


def test_rule9_an_absence_cannot_rest_on_profile_records_alone() -> None:
    unfamiliar = yes_no("Are you unfamiliar with TypeScript?")
    assert messages(unfamiliar, "Yes", (SKILLS,), absent="TypeScript") == [ABSENCE_ON_RECORDS]
    assert codes(yes_no("Do you have TypeScript experience?"), "No", evidence=(SKILLS, WORK),
                 absent="TypeScript") == ["unsupported"]
    # A stated fact cited with them: code lets it through, the verifier judges whether it covers the absence.
    assert codes(yes_no("Have you worked for Globex before?"), "No", evidence=(WORK, "history_complete"),
                 absent="Globex") == []


def test_rule10_a_profile_record_answer_says_what_it_rests_on() -> None:
    q = yes_no("Do you have Kafka experience?")
    assert messages(q, "Yes", (SKILLS,)) == [SAY_WHAT]
    assert codes(q, "Yes", evidence=(SKILLS,)) == ["uncited"]
    assert codes(q, "Yes", evidence=(SKILLS, WORK), quotes=["Kafka"]) == []
    assert codes(q, "No", evidence=(SKILLS, "history_complete"), absent="Kafka") == []
    # Not all records: a stated fact is cited, so nothing more is asked.
    assert codes(q, "Yes", evidence=(SKILLS, "auth")) == []
    # A stated fact of a record-like kind isn't a profile record.
    assert codes(q, "Yes", evidence=("work",)) == []


def test_rules8_to_10_leave_composed_and_stated_fact_answers_alone() -> None:
    prose = field("textarea", label="Tell us about your data work")
    assert codes(prose, "I enjoy building reliable Kafka pipelines.", composed=True, evidence=(SKILLS, WORK)) == []
    assert codes(yes_no("Authorized to work in the US?"), "Yes", evidence=("auth",)) == []
    assert codes(field("number", label="Years of Python?"), "6", evidence=("years_python",)) == []


COMPOSED_ON_A_FACT = "only an open-ended question gets a composed answer; answer this field from its facts"


def test_rule11_only_a_field_pass1_read_as_open_ended_gets_a_composed_answer() -> None:
    status = field("textarea", label="Briefly describe your US work authorization status.")
    text = "I am authorized to work in the United States."
    assert messages(status, text, ("auth",), composed=True, intent="US work authorization status") == [
        COMPOSED_ON_A_FACT]
    assert codes(status, text, composed=True, intent="US work authorization status") == ["invalid_value"]
    assert codes(status, text, composed=True, intent="") == ["invalid_value"]  # no interpretation: not open-ended
    assert codes(status, text, composed=True, intent="  Open-ended: motivation") == []
    assert codes(status, text, composed=True, intent=OPEN) == []
    # The same text stated from its fact is not composed: rule 11 doesn't apply.
    assert codes(status, text, intent="US work authorization status") == []
