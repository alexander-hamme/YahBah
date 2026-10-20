"""Typed screening facts (llm/screening.py): pure code, no models."""
import pytest

from yahbah.llm import screening as sc

YN = ["Yes", "No"]


@pytest.mark.parametrize("label, kinds", [
    ("Are you legally authorized to work in Germany?", {"work_authorization"}),
    ("Do you have the right to work in the UK?", {"work_authorization"}),
    ("Will you now or in the future require visa sponsorship?", {"sponsorship"}),
    ("Do you require a visa to work in the US?", {"sponsorship"}),
    ("Are you legally authorized to work in the United States without sponsorship?",
     {"work_authorization", "sponsorship"}),
    ("Are you a US citizen?", {"citizenship"}),
    ("Do you hold an active security clearance?", {"clearance"}),
    ("Available from", {"start_date"}),
    ("When can you start?", {"start_date"}),
    ("What is your notice period?", {"start_date"}),
    ("Desired salary", {"salary"}),
    ("Expected compensation", {"salary"}),
    ("Gender", {"eeo_gender"}), ("Race/Ethnicity", {"eeo_race"}), ("Are you Hispanic or Latino?", {"eeo_hispanic"}),
    ("Veteran status", {"eeo_veteran"}), ("Disability status", {"eeo_disability"}), ("Pronouns", {"eeo_pronouns"}),
    ("Are you at least 18 years of age?", {"age"}),
    ("Date of birth", {"age"}),
    ("Are you willing to relocate?", {"relocation"}),
    ("Have you ever been convicted of a felony?", {"criminal_record"}),
    ("Are you willing to undergo a background check?", {"background_check"}),
    ("Have you previously worked for Acme?", {"prior_employment"}),
    ("How many years of experience do you have with Python?", {"skill_years"}),
    ("Do you have 5+ years of professional experience with TypeScript and React?", {"skill_years"}),
    ("How many years of professional experience do you have?", {"total_years"}),
    ("Years of experience", {"total_years"}),
    # not screening questions
    ("Tell us about a time you debugged a race condition", set()),
    ("Have you led a relocation of services to the cloud?", set()),
    ("What's the secret to your productivity?", set()),
    ("What kind of fruit are you?", set()),
    ("Why do you want to work here?", set()),
    ("Do you have experience with PostgreSQL?", set()),
    ("Describe a project where you deployed a backend service to AWS.", set()),
])
def test_classify(label, kinds) -> None:
    assert sc.classify(label) == kinds


@pytest.mark.parametrize("label, skills", [
    ("How many years of experience do you have with Python?", ["python"]),
    ("Do you have 5+ years of professional experience with TypeScript and React?", ["typescript", "react"]),
    ("Years of Python experience", ["python"]),
    ("How many years have you worked with Kafka?", ["kafka"]),
    ("How many years of professional software development experience do you have?", ["software development"]),
    ("How many years of experience do you have in a professional setting?", []),
])
def test_skill_phrases(label, skills) -> None:
    assert sc.skill_phrases(label) == skills


@pytest.mark.parametrize("location, jd, expected", [
    ("Munich, BY, DE", "", "DE"), ("Austin, TX, USA", "", "US"), ("San Francisco, CA", "", "US"),
    ("Toronto, ON, CA", "", "CA"), ("London, England, GB", "", "UK"), ("Berlin, Germany", "", "DE"),
    ("Boston, MA | Munich, DE", "", sc.AMBIGUOUS),
    (None, "Software Engineer, Boston, MA (United States).", "US"),
    (None, "Backend Engineer, Munich, Germany.", "DE"),
    (None, "Backend Engineer, Munich, Germany. We are a US-founded company.", sc.AMBIGUOUS),
    ("Remote", "Join our team of builders.", None),
    ("Munich, Germany", "Software Engineer, Boston, MA (United States).", "DE"),  # the location wins
])
def test_job_jurisdiction(location, jd, expected) -> None:
    assert sc.job_jurisdiction(location, jd) == expected


def test_stored_keys_are_typed_by_name_and_scoped_by_jurisdiction() -> None:
    answers = {"work_authorization": "Yes", "sponsorship_required": "No", "work_status": "US Citizen",
               "work_authorization_germany": "No", "sponsorship_required_eu": "Yes", "years_python": "6",
               "security_clearance": "None", "gender_identity": "Decline", "how_did_you_hear": "Job board",
               "country_of_residence": "Canada"}
    typed = {(f.key, f.kind, f.scope) for f in sc.typed_facts(answers, {"years_of_experience": "7"})}
    assert typed == {
        ("work_authorization", "work_authorization", "CA"),  # unsuffixed: the country of residence
        ("sponsorship_required", "sponsorship", "CA"),
        ("work_status", "work_authorization", "US"), ("work_status", "citizenship", "US"),  # named in its value
        ("work_authorization_germany", "work_authorization", "DE"), ("sponsorship_required_eu", "sponsorship", "EU"),
        ("years_python", "skill_years", "python"), ("security_clearance", "clearance", None),
        ("gender_identity", "eeo_gender", None), ("years_of_experience", "total_years", None),
    }


FACTS = sc.typed_facts({"work_authorization": "Yes", "sponsorship_required": "No", "work_status": "US Citizen",
                        "years_python": "6", "gender_identity": "Decline to self-identify"}, {})


def resolve(label, options=(), field_type="radio", job_scope="US", preferred=None):
    return sc.resolve(label, field_type, list(options), sc.classify(label), FACTS, job_scope=job_scope, home="US",
                      preferred=preferred)


def test_resolution_in_code() -> None:
    r = resolve("Are you authorized to work in the US?", YN)
    assert (r.value, r.basis, r.derived) == ("Yes", ["work_authorization"], False)
    r = resolve("Can you work in the United States without requiring visa sponsorship?", ["Yes", "No"])
    assert (r.value, r.basis, r.derived) == ("Yes", ["sponsorship_required"], True)
    r = resolve("Gender", ["Male", "Female", "Decline to self-identify"], "select")
    assert (r.value, r.basis) == ("Decline to self-identify", ["gender_identity"])
    r = resolve("How many years of experience do you have with Python?", field_type="number")
    assert (r.value, r.basis) == ("6", ["years_python"])


def test_resolution_left_to_a_model_names_only_the_question_facts() -> None:
    r = resolve("Are you a US citizen?", YN)  # "US Citizen" is no yes/no: the model derives it
    assert r.value is None and [f.key for f in r.candidates] == ["work_status"]
    r = resolve("Are you a protected veteran?", YN)
    assert r.value is None and r.candidates == [] and "eeo_veteran" in r.missing


@pytest.mark.parametrize("label, job_scope", [
    ("Are you authorized to work in Germany?", "US"),
    ("Do you require sponsorship?", "DE"),
    ("Do you require sponsorship?", sc.AMBIGUOUS),
    ("Do you have 3+ years of experience with Go?", "US"),
])
def test_no_fact_no_answer(label, job_scope) -> None:
    r = resolve(label, YN, job_scope=job_scope)
    assert r.value is None and r.candidates == []


@pytest.mark.parametrize("number, options, expected", [
    (6, ["0-2", "3-5", "6-8", "9+"], "6-8"), (10, ["Less than 5", "5-10", "More than 10"], "5-10"),
    (11, ["Less than 5", "5-10", "More than 10"], "More than 10"), (3, ["1-3", "3-5"], "3-5"),
    (0.5, ["Less than 1 year", "1-3 years"], "Less than 1 year"), (4, ["Yes", "No"], None),
])
def test_pick_range(number, options, expected) -> None:
    assert sc.pick_range(number, options) == expected


@pytest.mark.parametrize("value, expected", [
    ("Yes", "yes"), ("No, I am a US citizen", "no"), ("I agree", "yes"), ("Now", None), ("Not sure", None),
    ("Decline to self-identify", None), ("N/A", None),
])
def test_yes_no(value, expected) -> None:
    assert sc.yes_no(value) == expected
