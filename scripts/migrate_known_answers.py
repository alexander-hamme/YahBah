"""
One-time migration: config/personal.yaml's flat `known_answers:` map -> a draft typed `facts:` list.

  uv run python scripts/migrate_known_answers.py           # writes config/personal.facts-draft.yaml
  uv run python scripts/migrate_known_answers.py --force   # overwrites an existing draft

The draft sits next to personal.yaml (gitignored like it); personal.yaml itself is never written.
Review the draft (each fact's kind, data_type, jurisdiction, description and guidance are guessed
from the old key name), move its facts: list into personal.yaml and delete known_answers: there.
Until then YahBah refuses to answer applications. Only counts and fact ids are printed, never values.

The key-name rules below live only in this tool: at runtime nothing types a fact by its id or kind.
"""
import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from yahbah.config import settings
from yahbah.llm.facts import Fact, is_placeholder

DRAFT_NAME = "personal.facts-draft.yaml"

HEADER = """\
# Draft typed facts, migrated from config/personal.yaml's known_answers: by
# scripts/migrate_known_answers.py. Kinds, data types, jurisdictions, descriptions and guidance were
# guessed from the old key names: review every entry (# REVIEW / # PLACEHOLDER mark the ones that
# need it most), fix or delete it, then move the facts: list below into config/personal.yaml and
# delete known_answers: there. Edits to personal.yaml apply to the next run.
facts:
"""

# Key suffixes (and country_of_residence values) naming a jurisdiction -> ISO 3166 alpha-2 or EU.
JURISDICTIONS = {"us": "US", "usa": "US", "united_states": "US", "united_states_of_america": "US",
                 "uk": "GB", "gb": "GB", "united_kingdom": "GB", "germany": "DE", "de": "DE", "eu": "EU",
                 "canada": "CA", "ca": "CA"}
COUNTRY_NAMES = {"US": "the United States", "GB": "the United Kingdom", "DE": "Germany", "EU": "the EU",
                 "CA": "Canada"}
HOME_DEFAULT = "US"

# Facts that are about one jurisdiction: key fullmatch (optionally suffixed _<country|code|eu>) -> hint.
# Unsuffixed keys describe known_answers.country_of_residence (default US).
JURISDICTIONAL_HINTS: list[tuple[str, dict]] = [
    (r"work_authori[sz]ation|authori[sz]ed_to_work|right_to_work|work_permit",
     {"kind": "work_authorization", "data_type": "boolean", "sensitivity": "legal",
      "description": "Whether the applicant is currently authorized to work in {country}",
      "guidance": "current authorization in {country} only"}),
    (r"sponsorship_required|requires?_sponsorship|needs?_sponsorship|visa_sponsorship(?:_required)?|sponsorship",
     {"kind": "sponsorship", "data_type": "boolean", "sensitivity": "legal",
      "description": "Whether the applicant needs visa sponsorship to work in {country}",
      "guidance": "now or in the future"}),
    (r"citizenship|citizen|work_status",
     {"kind": "citizenship", "data_type": "text", "sensitivity": "legal",
      "description": "The applicant's citizenship and work status"}),
]

# Everything else, in order: (regex, how it is matched, hint). A `fullmatch` on a well-known key is
# taken as is; a `search` hit is only a guess and marked for review.
LEGACY_HINTS: list[tuple[str, str, dict]] = [
    (r"years_of_experience|years_experience|total_years_of_experience|total_years_experience", "fullmatch",
     {"kind": "total_experience_years", "data_type": "number",
      "description": "The applicant's total years of professional experience"}),
    (r"years_(?:of_|with_|in_|using_)?(?P<skill>.+?)(?:_experience)?|(?P<skill2>.+)_years", "fullmatch",
     {"kind": "skill_experience_years", "data_type": "number",
      "description": "Years of professional {skill} experience"}),
    (r"salary_expectation", "fullmatch",
     {"kind": "salary_expectation", "description": "The applicant's salary expectation"}),
    (r"salary|compensation|pay_expectation|desired_pay|hourly_rate", "search",
     {"kind": "salary_expectation", "description": "The applicant's salary expectation"}),
    (r"start_date|^availab|notice|when_can_you_start", "search",
     {"kind": "start_availability", "data_type": "relative_availability",
      "description": "When the applicant can start a new job",
      "guidance": "Answer availability questions naturally. Do not invent a specific calendar date."}),
    (r"clearance", "search",
     {"kind": "security_clearance", "sensitivity": "legal",
      "description": "The applicant's security clearance"}),
    (r"transgender", "search",
     {"kind": "eeo_transgender", "sensitivity": "eeo", "description": "Self-identification: transgender identity"}),
    (r"sexual_orientation", "search",
     {"kind": "eeo_sexual_orientation", "sensitivity": "eeo", "description": "Self-identification: sexual orientation"}),
    (r"gender|^sex$", "search",
     {"kind": "eeo_gender", "sensitivity": "eeo", "description": "Self-identification: gender"}),
    (r"hispanic|latin", "search",
     {"kind": "eeo_hispanic_latino", "sensitivity": "eeo", "description": "Self-identification: Hispanic or Latino"}),
    (r"race|ethnic", "search",
     {"kind": "eeo_race", "sensitivity": "eeo", "description": "Self-identification: race / ethnicity"}),
    (r"veteran", "search",
     {"kind": "eeo_veteran", "sensitivity": "eeo", "description": "Self-identification: veteran status"}),
    (r"disabilit", "search",
     {"kind": "eeo_disability", "sensitivity": "eeo", "description": "Self-identification: disability status"}),
    (r"pronoun", "search",
     {"kind": "eeo_pronouns", "sensitivity": "eeo", "description": "Self-identification: pronouns"}),
    (r"(^|_)(18|21|age)(_|$)|legal_age|over_18|at_least_18", "search",
     {"kind": "minimum_age", "data_type": "boolean", "description": "Whether the applicant is at least 18 years old"}),
    (r"relocat", "search",
     {"kind": "relocation_willingness", "data_type": "boolean",
      "description": "Whether the applicant is willing to relocate for suitable employment",
      "guidance": "Willingness to relocate does not establish current residence in the destination country."}),
    (r"background_check|drug_(test|screen)", "search",
     {"kind": "background_check", "data_type": "boolean",
      "description": "Whether the applicant is willing to undergo a background check"}),
    (r"criminal|convict|felony", "search",
     {"kind": "criminal_record", "data_type": "boolean", "sensitivity": "legal",
      "description": "Whether the applicant has a criminal record"}),
    (r"worked_for|previously_employed|former_employee", "search",
     {"kind": "prior_employment", "data_type": "boolean",
      "description": "Whether the applicant has worked for the hiring company before"}),
    (r"non_compete|non_solicit", "search",
     {"kind": "non_compete", "data_type": "boolean", "sensitivity": "legal",
      "description": "Whether the applicant is bound by a non-compete or non-solicitation agreement"}),
    (r"zip|postal", "search",
     {"kind": "postal_code", "description": "The postal code where the applicant lives"}),
    (r"country_of_residence|state_of_residence|city_of_residence|residence", "search",
     {"kind": "residence", "description": "Where the applicant currently lives"}),
    (r"phone_code|country_code", "search",
     {"kind": "phone_country_code", "description": "The country calling code of the applicant's phone"}),
    (r"how_did_you_hear|how_i_found|found_this_job|hear_about|referral", "search",
     {"kind": "referral_source", "description": "How the applicant heard about the job"}),
    (r"which_location|location_preference|preferred_location|work_location", "search",
     {"kind": "work_location_preference", "description": "Where the applicant prefers to work"}),
    (r"consent|agree|allow_info_use", "search",
     {"kind": "consent_data_processing", "data_type": "boolean",
      "description": "Whether the applicant consents to the processing of their application data",
      "guidance": "consent to processing application data only; not a legal attestation"}),
]


def fact_id(key: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", key.lower())
    if not re.match(r"[a-z]", base):
        base = f"f_{base}"
    fid, n = base, 2
    while fid in taken:
        fid, n = f"{base}_{n}", n + 1
    taken.add(fid)
    return fid


def jurisdiction(name: str) -> str | None:
    """The jurisdiction code a key suffix or a country name stands for, else None."""
    norm = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return JURISDICTIONS.get(norm)


def hint_for(key: str, home: str) -> tuple[dict, bool]:
    """(fact fields guessed from the legacy key, whether the guess needs review)."""
    k = key.lower()
    for pattern, hint in JURISDICTIONAL_HINTS:
        m = re.fullmatch(rf"(?:{pattern})(?:_(?P<where>.+))?", k)
        if m:
            where = m.group("where")
            code = jurisdiction(where) if where else home
            country = COUNTRY_NAMES.get(code, code) if code else where.replace("_", " ")
            fields = {f: v.format(country=country) if isinstance(v, str) else v for f, v in hint.items()}
            return ({**fields, "jurisdiction": code} if code else fields), code is None
    for pattern, how, hint in LEGACY_HINTS:
        m = getattr(re, how)(pattern, k)
        if m:
            skill = (m.groupdict().get("skill") or m.groupdict().get("skill2") or "").replace("_", " ")
            return {f: v.format(skill=skill) if isinstance(v, str) else v for f, v in hint.items()}, how == "search"
    return {"kind": "unclassified", "description": f"Stored answer for: {key.replace('_', ' ')}"}, True


def draft_fact(fid: str, value: Any, hint: dict) -> tuple[Fact, bool]:
    """The validated fact; falls back to a text fact (flagged for review) if the value doesn't fit the
    guessed data_type."""
    if value is None:
        value = ""
    elif isinstance(value, (list, dict)):
        value = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    fields = {"id": fid, "kind": hint["kind"], "data_type": hint.get("data_type", "text"), "value": value,
              "description": hint["description"]}
    fields.update({f: hint[f] for f in ("guidance", "jurisdiction", "sensitivity") if hint.get(f)})
    try:
        return Fact.model_validate(fields), False
    except ValidationError:
        return Fact.model_validate({**fields, "data_type": "text"}), True


def fact_yaml(fact: Fact) -> str:
    out: dict[str, Any] = {"id": fact.id, "kind": fact.kind, "data_type": fact.data_type, "value": fact.value,
                           "description": fact.description}
    if fact.guidance:
        out["guidance"] = fact.guidance
    if fact.jurisdiction:
        out["jurisdiction"] = fact.jurisdiction
    if fact.sensitivity != "normal":
        out["sensitivity"] = fact.sensitivity
    return yaml.safe_dump([out], sort_keys=False, allow_unicode=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true", help="overwrite an existing draft")
    args = ap.parse_args(argv)

    personal = Path(settings.personal_path)
    draft = personal.with_name(DRAFT_NAME)
    legacy = (yaml.safe_load(personal.read_text()) or {}).get("known_answers") or {}
    if not legacy:
        print("nothing to migrate: personal.yaml has no known_answers")
        return 0
    if draft.exists() and not args.force:
        print(f"{draft} already exists: review it, or re-run with --force to overwrite it")
        return 1

    home = jurisdiction(str(legacy.get("country_of_residence") or "")) or HOME_DEFAULT
    taken: set[str] = set()
    chunks, review, placeholders = [HEADER], [], []
    for key, value in legacy.items():
        hint, guessed = hint_for(str(key), home)
        fact, retyped = draft_fact(fact_id(str(key), taken), value, hint)
        if guessed or retyped:
            review.append(fact.id)
        if guessed:
            chunks.append("# REVIEW: kind guessed from the key name\n")
        if retyped:
            chunks.append("# REVIEW: the value doesn't fit the guessed data_type; kept as text\n")
        if is_placeholder(fact.value):
            placeholders.append(fact.id)
            chunks.append("# PLACEHOLDER: replace or delete\n")
        chunks.append(fact_yaml(fact))
    draft.write_text("".join(chunks))

    print(f"wrote {draft}: {len(taken)} facts from {len(legacy)} known_answers")
    print(f"  to review ({len(review)}): {', '.join(review) or '-'}")
    print(f"  placeholders ({len(placeholders)}): {', '.join(placeholders) or '-'}")
    print("Next: review the draft, move its facts: list into personal.yaml, delete known_answers: there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
