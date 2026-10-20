"""The synthetic applicant used by every test. No real personal data: config/personal*.yaml is never read.

Facts are deliberately US-scoped (US citizen, authorized in the US, no clearance) so that questions
about other jurisdictions or unstored facts must end as NeedsAnswer.
"""
from pathlib import Path
from types import SimpleNamespace

import yaml

KNOWN_ANSWERS: dict[str, str] = {
    "work_authorization": "Yes",
    "sponsorship_required": "No",
    "work_status": "US Citizen",
    "willing_to_relocate": "Yes, willing to relocate",
    "currently_located_near_job": "No",
    "salary_expectation": "$150,000",
    "how_did_you_hear": "Job board",
}

EDUCATION = [{
    "school": "Example State University", "degree_type": "Bachelor of Science", "discipline": "Computer Science",
    "start_month": "September", "start_year": 2013, "end_month": "May", "end_year": 2017,
}]

WORK_EXPERIENCE = [{
    "title": "Software Engineer", "company": "Acme Data", "location": "Boston, MA", "duration": "2019 - now",
    "summary": "Built a Kafka ingestion service handling 2B events/day; led the move from cron jobs to Airflow.",
    "use_in_custom_prompts": True,
}]


def applicant(**overrides) -> SimpleNamespace:
    """An ApplicantProfile-shaped object (what FieldMapper.map and profile_text take)."""
    base = dict(
        full_name="Jane Q Doe", email="jane.doe@example.test", phone="+1 555 0100", location="Boston, MA",
        linkedin_url="https://www.linkedin.com/in/jane-doe-example", github_url=None, portfolio_url=None,
        years_of_experience=7, skills=["Python", "Kafka", "PostgreSQL"], work_experience=WORK_EXPERIENCE,
        education=EDUCATION, bio=None, resume_path="/nonexistent/resume.pdf",
    )
    return SimpleNamespace(**{**base, **overrides})


def personal_yaml(known_answers: dict[str, str] | None = None) -> str:
    """config/personal.yaml-shaped content for the synthetic applicant."""
    return yaml.safe_dump({
        "known_answers": dict(KNOWN_ANSWERS if known_answers is None else known_answers),
        "profile": {"education": EDUCATION},
    }, sort_keys=False)


def write_personal(path: Path, known_answers: dict[str, str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(personal_yaml(known_answers))
    return path
