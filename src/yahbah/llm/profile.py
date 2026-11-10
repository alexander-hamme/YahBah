"""
The applicant profile as the LLM layer sees it: one canonical profile, one serializer.

- Profile: the DB `applicant_profile` row (seeded from personal.yaml's `profile:` by
  scripts/seed_profile.py; anything with the same attributes works, e.g. tests' SimpleNamespace).
- `profile_facts()`: the profile fields as `key: value` pairs (`full_name`, `skills`,
  `work_experience[0]`, `education[0]`, ...). The answer engine's fact catalog (llm/facts.py) builds
  its `profile.<field>` facts from the same helpers.
- `profile_text()`: the only serializer of the profile as prompt text. Cover letters and match
  scoring use it, so a fact reads the same everywhere.
"""
from typing import Any

# Profile fields that are single values, in prompt order (first_name/last_name are derived).
SCALAR_FIELDS = ("full_name", "first_name", "last_name", "email", "phone", "location",
                 "linkedin_url", "github_url", "portfolio_url", "years_of_experience")
_CONTACT_FIELDS = {"email", "phone", "linkedin_url", "github_url", "portfolio_url"}


def _education(profile: Any) -> list[dict]:
    return list(getattr(profile, "education", None) or [])


def _experience(profile: Any) -> list[dict]:
    """Work-experience entries the applicant allows in prompts (`use_in_custom_prompts`, default yes)."""
    return [e for e in getattr(profile, "work_experience", None) or [] if e.get("use_in_custom_prompts", True)]


def scalar_values(profile: Any) -> dict[str, str]:
    """The single-value profile fields that are set, as strings."""
    first, _, last = (getattr(profile, "full_name", "") or "").partition(" ")
    values = {"first_name": first, "last_name": last}
    for key in SCALAR_FIELDS:
        if key not in values:
            value = getattr(profile, key, None)
            values[key] = "" if value is None else str(value)
    return {k: values[k] for k in SCALAR_FIELDS if values[k].strip()}


def _experience_line(e: dict) -> str:
    where = f", {e['location']}" if e.get("location") else ""
    head = f"{e.get('title', '')} at {e.get('company', '')}{where} ({e.get('duration', '')})"
    summary = " ".join(str(e.get("summary") or "").split())
    return f"{head}. {summary}" if summary else head


def _education_line(e: dict) -> str:
    degree = e.get("degree_type") or e.get("degree") or ""
    discipline = f" in {e['discipline']}" if e.get("discipline") else ""
    school = e.get("school") or e.get("institution") or ""
    years = "-".join(str(e[k]) for k in ("start_year", "end_year") if e.get(k)) or str(e.get("year") or "")
    gpa = f", GPA {e['gpa']}" if e.get("gpa") else ""
    return f"{degree}{discipline}, {school}" + (f" ({years})" if years else "") + gpa


def profile_facts(profile: Any) -> dict[str, str]:
    """Profile fields as `key: value` pairs: scalars, skills, bio, work_experience[i], education[i]."""
    facts = scalar_values(profile)
    skills = getattr(profile, "skills", None) or []
    if skills:
        facts["skills"] = ", ".join(skills)
    if (getattr(profile, "bio", None) or "").strip():
        facts["bio"] = " ".join(profile.bio.split())
    for i, e in enumerate(_experience(profile)):
        facts[f"work_experience[{i}]"] = _experience_line(e)
    for i, e in enumerate(_education(profile)):
        facts[f"education[{i}]"] = _education_line(e)
    return facts


def profile_text(profile: Any, *, contact: bool = True) -> str:
    """The profile as prompt text, one `key: value` line per fact (with each role's bullets).
    contact=False leaves out email, phone and links (match scoring, cover letters)."""
    return "\n".join(f"{k}: {v}" for k, v in profile_facts(profile).items()
                     if contact or k not in _CONTACT_FIELDS)

