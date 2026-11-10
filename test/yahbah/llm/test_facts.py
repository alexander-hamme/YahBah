"""The fact store (yahbah.llm.facts): personal.yaml facts validated and normalized, placeholders left
out, profile facts built in code. Synthetic data only (harness.profile)."""
import pytest
import yaml

from harness.profile import SYNTHETIC_FACTS, WORK_EXPERIENCE, applicant
from yahbah.config import load_prompts_config
from yahbah.llm.facts import Fact, FactsConfigError, is_placeholder, load_facts, yaml_fact_value


def write_raw(applicant_facts, text: str) -> None:
    """personal.yaml exactly as written by hand (YAML 1.1 typing included)."""
    applicant_facts.path.write_text(text)
    load_prompts_config.cache_clear()


def fact(**kw) -> dict:
    return {"id": "f", "kind": "k", "description": "a fact", **kw}


# ── Normalization ─────────────────────────────────────────────────────────────

def test_yaml_typed_scalars_are_normalized_by_data_type(applicant_facts) -> None:
    write_raw(applicant_facts, """
facts:
  - {id: relocate_text, kind: relocation, value: Yes, description: d}
  - {id: years_text, kind: years, value: 6, description: d}
  - {id: start_text, kind: start, value: 2026-01-15, description: d}
  - {id: auth, kind: work_authorization, data_type: boolean, value: "no", description: d}
  - {id: auth_bool, kind: work_authorization, data_type: boolean, value: true, description: d}
  - {id: years, kind: skill_experience_years, data_type: number, value: "6", description: d}
  - {id: gpa, kind: gpa, data_type: number, value: "3.5", description: d}
  - {id: start, kind: start_date, data_type: date, value: 2026-01-15, description: d}
  - {id: start_str, kind: start_date, data_type: date, value: "2026-02-01", description: d}
  - {id: langs, kind: languages, data_type: list, value: [English, German], description: d}
  - {id: notice, kind: availability, data_type: relative_availability, value: "2 weeks", description: d}
""")
    values = {i: f.value for i, f in load_facts(applicant()).facts.items() if not i.startswith("profile.")}
    assert values == {"relocate_text": "Yes", "years_text": "6", "start_text": "2026-01-15", "auth": False,
                      "auth_bool": True, "years": 6, "gpa": 3.5, "start": "2026-01-15", "start_str": "2026-02-01",
                      "langs": ["English", "German"], "notice": "2 weeks"}
    assert isinstance(values["years"], int) and isinstance(values["auth"], bool)


@pytest.mark.parametrize("data_type,value", [
    ("boolean", "maybe"), ("number", "six"), ("number", True), ("date", "2026-02-30"), ("date", "15/01/2026"),
    ("list", "English"), ("list", ["English", 3]), ("relative_availability", 2), ("text", ["a"]),
])
def test_values_that_dont_fit_their_data_type_are_rejected(data_type, value) -> None:
    with pytest.raises(ValueError):
        Fact.model_validate(fact(data_type=data_type, value=value))


# ── Validation errors name the problem, never the value ───────────────────────

def test_validation_errors_name_index_and_field_without_values(applicant_facts) -> None:
    secret = "Secret-Value-4711"
    applicant_facts.set_facts([
        {"id": "ok", "kind": "k", "value": "fine", "description": "d"},
        {"id": "Bad-Id", "kind": "k", "value": secret, "description": "d"},
        {"id": "b", "kind": "k", "data_type": "boolean", "value": secret, "description": "d"},
        {"id": "c", "kind": "k", "value": secret, "description": "d", "jurisdiction": "Germany"},
        {"id": "d", "kind": "k", "value": secret, "description": "d", "source": "profile"},
        {"id": "e", "kind": "k", "value": secret, "description": "  "},
        {"id": "f", "kind": "k", "value": secret, "description": "d", "secret_key": secret},
        {"id": "ok", "kind": "k", "value": secret, "description": "d"},
    ])
    with pytest.raises(FactsConfigError) as exc:
        load_facts(applicant())
    message = str(exc.value)
    assert secret not in message
    for where in ("facts[1].id", "facts[2].value", "facts[3].jurisdiction", "facts[4].source", "facts[5].description",
                  "facts[6].secret_key", "facts[7].id: duplicate id 'ok'"):
        assert where in message
    assert "facts[0]" not in message


def test_a_missing_or_empty_facts_list_is_valid(applicant_facts) -> None:
    write_raw(applicant_facts, "profile: {}\n")
    assert load_prompts_config()["facts"] == []
    catalog = load_facts(applicant())
    assert all(i.startswith("profile.") for i in catalog.facts) and catalog.placeholders == ()
    write_raw(applicant_facts, "facts:\n")
    assert load_prompts_config()["facts"] == []


# ── Placeholders ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "", "  ", "<your answer>", "TODO", "tbd later", "Replace me", "lorem ipsum dolor", "Your city", "000",
    "555-555-5555", "+1 555 555 5555", "me@example.com", "https://example.org/x", [],
])
def test_placeholder_values(value) -> None:
    assert is_placeholder(value)


@pytest.mark.parametrize("value", [
    "Boston", "No", "0 days", "+1 555 0100", "jane@example.test", "Toronto", "Yourself first", 6, False, ["Python"],
])
def test_real_values_are_not_placeholders(value) -> None:
    assert not is_placeholder(value)


def test_placeholder_facts_are_left_out_and_reported(applicant_facts) -> None:
    applicant_facts.add({"id": "clearance", "kind": "security_clearance", "value": "<fill in>", "description": "d"})
    applicant_facts.add({"id": "notice", "kind": "availability", "value": "TBD", "description": "d"})
    catalog = load_facts(applicant())
    assert catalog.placeholders == ("clearance", "notice")
    assert "clearance" not in catalog.facts and "notice" not in catalog.facts
    assert "residence" in catalog.facts


# ── Profile facts ─────────────────────────────────────────────────────────────

def test_profile_facts_follow_yaml_facts_and_the_alias_is_the_email(applicant_facts) -> None:
    catalog = load_facts(applicant(), account_email="jane+run7@example.test")
    ids = list(catalog.facts)
    assert ids[:len(SYNTHETIC_FACTS)] == [f["id"] for f in SYNTHETIC_FACTS]
    assert ids[len(SYNTHETIC_FACTS):] == [
        "profile.full_name", "profile.first_name", "profile.last_name", "profile.email", "profile.phone",
        "profile.location", "profile.linkedin_url", "profile.years_of_experience", "profile.skills",
        "profile.work_experience[0]", "profile.education[0]"]  # (github_url, portfolio_url, bio are empty)
    f = catalog.facts
    assert f["profile.email"].value == "jane+run7@example.test"
    assert (f["profile.first_name"].value, f["profile.last_name"].value) == ("Jane", "Q Doe")
    assert (f["profile.years_of_experience"].data_type, f["profile.years_of_experience"].value) == ("number", 7)
    assert f["profile.skills"].value == ["Python", "Kafka", "PostgreSQL"]
    assert f["profile.location"].description == "Where the applicant currently lives"
    assert all(x.source == "profile" for i, x in f.items() if i.startswith("profile."))
    work = f["profile.work_experience[0]"]
    assert work.kind == "work_experience" and work.value.startswith("Software Engineer at Acme Data, Boston, MA (2019 - now). ")
    assert work.description == "Work experience: Software Engineer at Acme Data (2019 - now)"
    edu = f["profile.education[0]"]
    assert edu.value == "Bachelor of Science in Computer Science, Example State University (2013-2017)"
    assert edu.description == "Education: Bachelor of Science in Computer Science, Example State University"
    assert load_facts(applicant()).facts["profile.email"].value == "jane.doe@example.test"


def test_hidden_work_experience_is_not_a_fact(applicant_facts) -> None:
    hidden = [{**WORK_EXPERIENCE[0], "use_in_custom_prompts": False}]
    facts = load_facts(applicant(work_experience=hidden)).facts
    assert not any(i.startswith("profile.work_experience") for i in facts) and "profile.education[0]" in facts


# ── Catalog views ─────────────────────────────────────────────────────────────

def test_compact_has_no_values_blocks_do(applicant_facts) -> None:
    catalog = load_facts(applicant())
    compact = catalog.compact(["residence"])
    assert compact == [{"id": "residence", "kind": "residence", "data_type": "text",
                        "description": "Where the applicant currently lives", "aliases": [], "examples": [],
                        "jurisdiction": "US"}]
    assert "Boston" not in str(catalog.compact())
    block = catalog.blocks(["relocation", "nope"])
    assert len(block) == 1 and block[0]["value"] is True and block[0]["guidance"].startswith("Willingness")
    assert set(block[0]) == {"id", "kind", "data_type", "value", "description", "aliases", "examples", "guidance",
                             "jurisdiction", "applies_to", "source", "sensitivity"}
    assert catalog.values(["profile.skills", "relocation"]) == ["Python, Kafka, PostgreSQL", "True"]


def test_yaml_fact_value_reads_the_first_fact_of_a_kind(applicant_facts) -> None:
    assert yaml_fact_value("salary_expectation") == "Open to discussion"
    assert yaml_fact_value("name") is None  # profile facts don't count
    applicant_facts.add({"id": "x", "kind": "k", "value": "v"})  # no description: invalid
    assert yaml_fact_value("salary_expectation") is None


def test_an_edited_fact_applies_to_the_next_load(applicant_facts) -> None:
    applicant_facts.set_value("residence", "Denver, CO, United States")
    assert load_facts(applicant()).facts["residence"].value == "Denver, CO, United States"
    applicant_facts.remove("residence")
    assert "residence" not in load_facts(applicant()).facts


def test_a_leftover_known_answers_map_fails_loudly_with_the_migration_steps(applicant_facts) -> None:
    write_raw(applicant_facts, yaml.safe_dump({"known_answers": {"work_authorization": "Yes"},
                                               "facts": SYNTHETIC_FACTS}))
    with pytest.raises(FactsConfigError) as exc:
        load_facts(applicant())
    assert "scripts/migrate_known_answers.py" in str(exc.value) and "Yes" not in str(exc.value)
    assert "known_answers" not in load_prompts_config()  # the old map is never read


def test_the_example_personal_yaml_is_a_valid_fact_list(applicant_facts) -> None:
    from harness.env import REPO

    write_raw(applicant_facts, (REPO / "config" / "personal.example.yaml").read_text())
    catalog = load_facts(applicant())
    assert {"availability_notice", "relocation", "work_authorization_us"} <= set(catalog.facts)
    assert set(catalog.placeholders) == {"residence", "postal_code"}  # the example's own dummy values
    assert catalog.facts["availability_notice"].data_type == "relative_availability"
