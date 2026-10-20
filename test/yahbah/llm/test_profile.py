"""The canonical profile view (llm/profile.py), the cover-letter gate, and prompts.yaml freshness."""
from harness.profile import applicant
from yahbah.config import load_prompts_config
from yahbah.llm import profile as pv
from yahbah.llm.cover_letter import CoverLetterGenerator, cover_letter_requested


def test_profile_text_has_every_fact_keyed_with_the_bullets() -> None:
    text = pv.profile_text(applicant())
    assert "work_experience[0]: Software Engineer at Acme Data, Boston, MA (2019 - now). Built a Kafka" in text
    assert "education[0]: Bachelor of Science in Computer Science, Example State University (2013-2017)" in text
    assert "skills: Python, Kafka, PostgreSQL" in text and "email: jane.doe@example.test" in text
    assert "github_url" not in text  # unset fields are left out, not shown as N/A
    assert "email" not in pv.profile_text(applicant(), contact=False)


def test_entries_kept_out_of_prompts_are_not_facts() -> None:
    hidden = {"title": "Intern", "company": "Secret Lab", "duration": "2018", "use_in_custom_prompts": False}
    profile = applicant(work_experience=[*applicant().work_experience, hidden])
    assert "Secret Lab" not in pv.profile_text(profile)
    assert "work_experience[1]" not in pv.facts(profile, {})


def test_stored_answers_include_education_keys_from_the_profile(applicant_facts) -> None:
    stored = pv.stored_answers(applicant())
    assert stored["education_school"] == "Example State University" and stored["sponsorship_required"] == "No"
    applicant_facts.update(education_school="Override U")
    assert pv.stored_answers(applicant())["education_school"] == "Override U"  # an explicit answer wins


def test_prompts_config_is_reread_when_a_file_changes(applicant_facts) -> None:
    before = load_prompts_config()
    assert load_prompts_config() is before  # unchanged files: no re-read
    applicant_facts.path.write_text(applicant_facts.path.read_text() + "\n# edited\n")
    assert load_prompts_config() is not before


def test_cover_letter_only_when_the_form_asks_for_one() -> None:
    assert cover_letter_requested([{"source": "resume"}, {"source": "cover_letter"}])
    assert not cover_letter_requested([{"source": "resume"}, {"source": "custom"}])


async def test_a_cover_letter_may_not_introduce_numbers(llm, applicant_facts) -> None:
    llm.on("prose", replies=["I grew revenue 40%.\n\nAt Acme Data I built a Kafka ingestion service handling 2B "
                             "events/day.", "I grew revenue 40% again.\n\nAt Acme Data I led the move to Airflow."])
    text = await CoverLetterGenerator().generate("Acme Robotics, Boston, MA. 500 customers.", applicant())
    assert text == "At Acme Data I led the move to Airflow."
    first = llm.calls_for("prose")[0]
    assert "Never invent employers" in first.system and "Kafka ingestion service" in first.user
