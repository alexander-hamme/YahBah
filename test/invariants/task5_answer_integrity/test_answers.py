"""Facts are retrieved, narratives are generated, nothing generated asserts a new fact (roadmap A5, B6;
contract §8 basis/generated; audit LLM F1/F2/F4, QA F4).

FieldMapper runs for real with both models scripted (the driver routes every field to `custom`; the
prose model plays the adversary of each case) against the synthetic applicant (harness.profile:
US citizen, authorized in the US, no clearance). The answer cache is the real answer_cache table.
"""
import pytest

from harness.contract import defect
from harness.llm import answer, map_all
from harness.profile import applicant
from yahbah.agent.fill import match_option
from yahbah.llm.field_mapper import FieldMapper, NeedsAnswer, profile_text
from yahbah.schemas import FormField

US_JOB = "Software Engineer, Boston, MA (United States). We build data pipelines in Python."


def field(label: str, field_type: str = "text", options=None, required: bool = True, key: str = "1") -> FormField:
    return FormField(key=key, label=label, field_type=field_type, required=required, options=options or [])


@pytest.fixture
def mapper(db, llm, applicant_facts):
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    return applicant_facts


async def cached(db) -> list[tuple[str, str]]:
    return [(r["question"], r["answer"]) for r in await db.fetchall("SELECT question, answer FROM answer_cache")]


@defect("A5", 5, "an applicant fact backed only by job-description text is typed in and cached")
async def test_a_clearance_claim_backed_only_by_the_job_description_needs_an_answer(db, llm, mapper) -> None:
    jd = "Requirements: 10+ years of Kubernetes. Candidates must hold an active security clearance."
    llm.on("prose", handler=lambda call: answer("Yes", quotes=["must hold an active security clearance"]))
    with pytest.raises(NeedsAnswer):
        await FieldMapper().map([field("Do you hold an active security clearance?", "select", ["Yes", "No"])],
                                applicant(), job_description=jd)
    assert await cached(db) == []


@defect("A5", 5, "quotes of unrelated stored answers pass the evidence check (live run 2026-10-17)")
async def test_a_start_date_backed_by_irrelevant_stored_answers_needs_an_answer(db, llm, mapper) -> None:
    llm.on("prose", handler=lambda call: answer(
        "Immediately", quotes=["willing_to_relocate: Yes, willing to relocate"],
        basis=["willing_to_relocate", "currently_located_near_job"]))
    with pytest.raises(NeedsAnswer):
        await FieldMapper().map([field("Available from")], applicant(), job_description=US_JOB)
    assert await cached(db) == []


@defect("A5", 5, "US-only authorization facts are generalized to another jurisdiction (live probe: Germany -> Yes)")
async def test_work_authorization_for_another_country_needs_an_answer(db, llm, mapper) -> None:
    llm.on("prose", handler=lambda call: answer(
        "Yes", quotes=["work_authorization: Yes", "work_status: US Citizen"], basis=["work_authorization", "work_status"]))
    with pytest.raises(NeedsAnswer):
        await FieldMapper().map([field("Are you legally authorized to work in Germany?", "radio", ["Yes", "No"])],
                                applicant(), job_description="Backend Engineer, Munich, Germany.")
    assert await cached(db) == []


@defect("A5", 5, "the answer cache is read before the stored answers and never invalidated")
async def test_an_updated_stored_answer_wins_over_the_cache(db, llm, mapper) -> None:
    def faithful(call):  # answers from the stored sponsorship fact it is shown
        current = mapper.known_answers["sponsorship_required"]
        return answer(current, quotes=[f"sponsorship_required: {current}"], basis=["sponsorship_required"])

    llm.on("prose", handler=faithful)
    question = field("Will you now or in the future require visa sponsorship?", "radio", ["Yes", "No"])
    first = await FieldMapper().map([question], applicant(), job_description=US_JOB)
    assert first[0].value == "No"
    mapper.update(sponsorship_required="Yes")
    second = await FieldMapper().map([question], applicant(), job_description=US_JOB)
    assert second[0].value == "Yes", "a cached answer shadows the updated stored answer"


@defect("A5", 5, "conditional and company-specific answers are cached by label for every later form")
@pytest.mark.parametrize("label, reply, jd", [
    ("If yes, please explain", answer("N/A", quotes=["sponsorship_required: No"], basis=["sponsorship_required"]), US_JOB),
    ("Why do you want to join Acme Robotics?",
     answer("I want to build data infrastructure at Acme Robotics.", quotes=["Acme Robotics"], generated=True),
     "Acme Robotics is hiring a data engineer in Boston, MA."),
], ids=["conditional_follow_up", "company_specific"])
async def test_context_dependent_answers_are_not_cached(db, llm, mapper, label, reply, jd) -> None:
    llm.on("prose", handler=lambda call: reply)
    await FieldMapper().map([field(label, "textarea", required=False)], applicant(), job_description=jd)
    assert await cached(db) == []


@defect("A5", 5, "a required open-ended question with nothing quotable ends the run as NeedsAnswer")
async def test_a_required_curveball_is_answered_and_labelled_generated(db, llm, mapper) -> None:
    llm.on("prose", handler=lambda call: answer(
        "A pineapple: prickly on the outside, but I build things that hold up, like a Kafka pipeline at 2B events/day.",
        generated=True))
    out = await FieldMapper().map([field("What kind of fruit are you?", "textarea")], applicant(),
                                  job_description=US_JOB)
    assert out[0].value.startswith("A pineapple")
    assert out[0].generated is True


@defect("A5 (match_option)", 5, "character-prefix matching turns a stored 'No' into another option")
@pytest.mark.parametrize("options, wrong", [(["Now", "In the future", "Never"], "Now"), (["Yes", "Not sure"], "Not sure")])
def test_no_is_never_matched_to_a_longer_word(options, wrong) -> None:
    assert match_option("No", options) != wrong


@defect("B6", 5, "profile_text drops the work-experience bullets the prose prompt tells the model to use")
def test_profile_text_includes_the_work_experience_bullets() -> None:
    assert "Kafka ingestion service" in profile_text(applicant())
