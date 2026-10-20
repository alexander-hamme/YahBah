"""
The applicant as the LLM layer sees it: one canonical profile, one fact namespace, one serializer.

- Profile: the DB `applicant_profile` row (seeded from personal.yaml's `profile:` by
  scripts/seed_profile.py; anything with the same attributes works, e.g. tests' SimpleNamespace).
- Stored answers: personal.yaml `known_answers` (re-read when the file changes), plus education
  keys derived from the profile's most recent education entry.
- `facts()`: every key an answer may cite in `basis`, with its current value: the stored answers,
  then the profile fields (`full_name`, `skills`, `work_experience[0]`, `education[0]`, ...).
  A key that is not in it can never back an answer; the job description is never in it.
- `profile_text()`: the only serializer of the profile. Field mapping, custom answers, cover
  letters and match scoring all use it, so a fact reads the same everywhere, and every line starts
  with the key a model cites for it.
"""
from typing import Any

from yahbah.config import load_prompts_config

# Profile fields that are single values, in prompt order (first_name/last_name are derived).
SCALAR_FIELDS = ("full_name", "first_name", "last_name", "email", "phone", "location",
                 "linkedin_url", "github_url", "portfolio_url", "years_of_experience")
_CONTACT_FIELDS = {"email", "phone", "linkedin_url", "github_url", "portfolio_url"}
_EDUCATION_KEYS = ("school", "degree_type", "discipline", "start_month", "start_year", "end_month",
                   "end_year", "gpa")


def known_answers() -> dict[str, str]:
    """personal.yaml known_answers, as strings; empty values are left out (they back nothing)."""
    raw = load_prompts_config().get("known_answers") or {}
    return {str(k): str(v).strip() for k, v in raw.items() if v is not None and str(v).strip()}


def _education(profile: Any) -> list[dict]:
    return list(getattr(profile, "education", None) or [])


def _experience(profile: Any) -> list[dict]:
    """Work-experience entries the applicant allows in prompts (`use_in_custom_prompts`, default yes)."""
    return [e for e in getattr(profile, "work_experience", None) or [] if e.get("use_in_custom_prompts", True)]


def education_answers(profile: Any) -> dict[str, str]:
    """education_<key> stored answers from the profile's most recent education entry."""
    entries = _education(profile)
    if not entries:
        return {}
    return {f"education_{k}": str(entries[0][k]) for k in _EDUCATION_KEYS if entries[0].get(k)}


def stored_answers(profile: Any) -> dict[str, str]:
    """Known answers plus the education-derived keys (an explicit known answer wins)."""
    return {**education_answers(profile), **known_answers()}


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
    """Profile fields as citable facts: scalars, skills, bio, work_experience[i], education[i]."""
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


def facts(profile: Any, stored: dict[str, str] | None = None) -> dict[str, str]:
    """Every key an answer may cite, with its current value. A stored answer wins over a profile
    field of the same name (as in field mapping)."""
    return {**profile_facts(profile), **(stored_answers(profile) if stored is None else stored)}


def profile_text(profile: Any, *, contact: bool = True) -> str:
    """The profile as prompt text, one `key: value` line per fact (with each role's bullets).
    contact=False leaves out email, phone and links (match scoring, cover letters)."""
    return "\n".join(f"{k}: {v}" for k, v in profile_facts(profile).items()
                     if contact or k not in _CONTACT_FIELDS)


def stored_text(stored: dict[str, str]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in stored.items())
