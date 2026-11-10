"""The synthetic applicant used by every test. No real personal data: config/personal*.yaml is never read.

Facts are deliberately US-scoped (US citizen, authorized in the US, no clearance) so that questions
about other jurisdictions or unstored facts must end as NeedsAnswer.
"""
import copy
from pathlib import Path
from types import SimpleNamespace

import yaml

# The applicant's typed facts (config/personal.yaml `facts:`), read by the answer engine.
SYNTHETIC_FACTS: list[dict] = [
    {"id": "citizenship_us", "kind": "citizenship", "value": "US Citizen", "sensitivity": "legal",
     "jurisdiction": "US", "description": "The applicant's citizenship"},
    {"id": "work_authorization_us", "kind": "work_authorization", "data_type": "boolean", "value": True,
     "sensitivity": "legal", "jurisdiction": "US", "guidance": "current authorization in the US only",
     "description": "Whether the applicant is currently authorized to work in the United States"},
    {"id": "sponsorship_required_us", "kind": "sponsorship", "data_type": "boolean", "value": False,
     "sensitivity": "legal", "jurisdiction": "US", "guidance": "now or in the future",
     "description": "Whether the applicant needs visa sponsorship to work in the United States"},
    {"id": "residence", "kind": "residence", "value": "Boston, MA, United States", "jurisdiction": "US",
     "description": "Where the applicant currently lives"},
    {"id": "relocation", "kind": "relocation", "data_type": "boolean", "value": True,
     "guidance": "Willingness to relocate does not establish current residence in the destination country.",
     "description": "Whether the applicant is willing to relocate for a job"},
    {"id": "referral_source", "kind": "referral_source", "value": "Job board",
     "description": "How the applicant heard about job openings"},
    {"id": "salary_expectation", "kind": "salary_expectation", "value": "Open to discussion",
     "description": "The applicant's salary expectation"},
]

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
    """An ApplicantProfile-shaped object (what load_facts and profile_text take)."""
    base = dict(
        full_name="Jane Q Doe", email="jane.doe@example.test", phone="+1 555 0100", location="Boston, MA",
        linkedin_url="https://www.linkedin.com/in/jane-doe-example", github_url=None, portfolio_url=None,
        years_of_experience=7, skills=["Python", "Kafka", "PostgreSQL"], work_experience=WORK_EXPERIENCE,
        education=EDUCATION, bio=None, resume_path="/nonexistent/resume.pdf",
    )
    return SimpleNamespace(**{**base, **overrides})


def personal_yaml(facts: list[dict] | None = None) -> str:
    """config/personal.yaml-shaped content for the synthetic applicant."""
    return yaml.safe_dump({
        "facts": copy.deepcopy(SYNTHETIC_FACTS if facts is None else facts),
        "profile": {"education": EDUCATION},
    }, sort_keys=False)


def write_personal(path: Path, facts: list[dict] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(personal_yaml(facts))
    return path
