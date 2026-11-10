"""
The applicant's fact store: what every answer on an application form rests on.

Two sources, one catalog:
- YAML facts: the typed `facts:` list in config/personal.yaml (`source: applicant_verified`),
  validated here. A fact still holding a placeholder value (empty, `<...>`, "TODO", a 555 phone
  number, an example.com address, ...) is left out and reported in `FactCatalog.placeholders`.
- Profile facts: the ApplicantProfile row as `profile.<field>` facts (`source: profile`), built in
  code. The email fact is the run's alias when one is given.

A personal.yaml that still has the old flat `known_answers:` map is refused (FactsConfigError with
the migration steps) until scripts/migrate_known_answers.py's draft replaces it.

Kinds and descriptions are retrieval aids for the answer models; nothing in code types a fact by
its id or kind. Validation messages name the fact index, the field and the problem, never a value.
"""
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from yahbah.config import load_prompts_config
from yahbah.llm.profile import _education, _education_line, _experience, _experience_line, scalar_values

__all__ = ["Fact", "FactCatalog", "FactsConfigError", "is_placeholder", "load_facts", "value_text", "yaml_fact_value"]

DataType = Literal["text", "boolean", "number", "date", "relative_availability", "list"]

_YAML_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_KIND = re.compile(r"^[a-z][a-z0-9_]*$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_BOOLEANS = {"yes": True, "true": True, "no": False, "false": False}
_FROM_YAML = "yaml"  # validation context: the fact comes from personal.yaml
_LEGACY_KNOWN_ANSWERS = ('config/personal.yaml still has known_answers: run "uv run python '
                         'scripts/migrate_known_answers.py", review config/personal.facts-draft.yaml, move its '
                         'facts: list into personal.yaml and delete known_answers.')


class FactsConfigError(Exception):
    """config/personal.yaml's facts: list is invalid. The message names each problem, never a value."""


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: str
    # data_type precedes value so the value is normalized by its declared type.
    data_type: DataType = "text"
    value: str | int | float | bool | list[str]
    description: str
    aliases: list[str] = []
    examples: list[str] = []  # example questions
    guidance: str = ""
    jurisdiction: str | None = Field(default=None, pattern=r"^([A-Z]{2}|EU)$")
    applies_to: str = ""
    source: Literal["applicant_verified", "profile"] = "applicant_verified"
    sensitivity: Literal["normal", "legal", "eeo"] = "normal"

    @field_validator("id")
    @classmethod
    def _id(cls, v: str, info: ValidationInfo) -> str:
        if info.context == _FROM_YAML and not _YAML_ID.match(v):
            raise ValueError("must be snake_case: a lowercase letter, then lowercase letters, digits or _")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if not _KIND.match(v):
            raise ValueError("must be snake_case: a lowercase letter, then lowercase letters, digits or _")
        return v

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, v: Any, info: ValidationInfo) -> Any:
        data_type = info.data.get("data_type")
        return v if data_type is None else _normalize(data_type, v)  # (an invalid data_type is reported already)

    @field_validator("description")
    @classmethod
    def _description(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("source")
    @classmethod
    def _source(cls, v: str, info: ValidationInfo) -> str:
        if info.context == _FROM_YAML and v == "profile":
            raise ValueError("'profile' is reserved for facts built from the applicant profile")
        return v


def _normalize(data_type: str, v: Any) -> Any:
    """The value as its data_type stores it. YAML 1.1 reads `Yes` as true and `2026-01-15` as a date,
    so a text fact accepts those and keeps their text."""
    if data_type == "text":
        if isinstance(v, bool):
            return "Yes" if v else "No"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, str):
            return v.strip()
        raise ValueError("a text fact needs a single text value")
    if data_type == "boolean":
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.strip().casefold() in _BOOLEANS:
            return _BOOLEANS[v.strip().casefold()]
        raise ValueError("a boolean fact needs true/false or yes/no")
    if data_type == "number":
        if isinstance(v, bool):
            raise ValueError("a number fact needs a number")
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str) and _NUMBER.match(v.strip()):
            return float(v) if "." in v else int(v)
        raise ValueError("a number fact needs a number")
    if data_type == "date":
        if isinstance(v, datetime):
            return v.date().isoformat()
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, str) and _ISO_DATE.match(v.strip()):
            try:
                return date.fromisoformat(v.strip()).isoformat()
            except ValueError:
                pass
        raise ValueError("a date fact needs a real date as YYYY-MM-DD")
    if data_type == "list":
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return [x.strip() for x in v]
        raise ValueError("a list fact needs a list of texts")
    if isinstance(v, str):  # relative_availability
        return v.strip()
    raise ValueError("a relative_availability fact needs a text value")


def value_text(value: Any) -> str:
    """A fact value as text: lists joined with ", "."""
    return ", ".join(value) if isinstance(value, list) else str(value)


_PLACEHOLDER_RES = (
    re.compile(r"<[^>]+>"),
    re.compile(r"\b(todo|tbd|tba|fixme|changeme|replace me|lorem ipsum)\b", re.IGNORECASE),
    re.compile(r"^your\s", re.IGNORECASE),
    re.compile(r"^0+$"),
    re.compile(r"^(\+?1[\s.-]?)?555[\s.-]?555[\s.-]?5555$"),
    re.compile(r"example\.(com|org|net)", re.IGNORECASE),
)


def is_placeholder(value: Any) -> bool:
    """A YAML fact value that was never filled in (template text, a dummy number or address)."""
    text = value_text(value).strip()
    return not text or any(r.search(text) for r in _PLACEHOLDER_RES)


@dataclass(frozen=True)
class FactCatalog:
    facts: dict[str, Fact]          # YAML facts first (file order), then profile facts
    placeholders: tuple[str, ...]   # YAML fact ids ignored as placeholders

    def _select(self, ids: Iterable[str] | None) -> list[Fact]:
        if ids is None:
            return list(self.facts.values())
        return [self.facts[i] for i in dict.fromkeys(ids) if i in self.facts]

    def compact(self, ids: Iterable[str] | None = None) -> list[dict]:
        """What a model needs to pick facts, without their values."""
        return [{"id": f.id, "kind": f.kind, "data_type": f.data_type, "description": f.description,
                 "aliases": list(f.aliases), "examples": list(f.examples), "jurisdiction": f.jurisdiction}
                for f in self._select(ids)]

    def blocks(self, ids: Iterable[str]) -> list[dict]:
        """The full facts, values included: the evidence an answer is composed and checked against."""
        return [f.model_dump() for f in self._select(ids)]

    def values(self, ids: Iterable[str] | None = None) -> list[str]:
        return [value_text(f.value) for f in self._select(ids)]


def _yaml_facts() -> tuple[list[Fact], list[str]]:
    """personal.yaml's facts, validated: (usable facts, placeholder ids). Raises FactsConfigError."""
    config = load_prompts_config()
    if config.get("legacy_known_answers"):
        raise FactsConfigError(_LEGACY_KNOWN_ANSWERS)
    raw = config.get("facts") or []
    if not isinstance(raw, list):
        raise FactsConfigError("facts: must be a list of facts")
    errors: list[str] = []
    facts: list[Fact] = []
    placeholders: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"facts[{i}]: must be a mapping of fact fields")
            continue
        try:
            fact = Fact.model_validate(item, context=_FROM_YAML)
        except ValidationError as exc:
            errors.extend(f"facts[{i}].{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
            continue
        if fact.id in seen:
            errors.append(f"facts[{i}].id: duplicate id {fact.id!r}")
            continue
        seen.add(fact.id)
        if is_placeholder(fact.value):
            placeholders.append(fact.id)
        else:
            facts.append(fact)
    if errors:
        raise FactsConfigError("config/personal.yaml facts: " + "; ".join(errors))
    return facts, placeholders


# One-line descriptions of the fixed profile fields (fact id "profile.<field>").
_PROFILE_FIELDS: dict[str, tuple[str, DataType, str]] = {
    "full_name": ("name", "text", "The applicant's full name"),
    "first_name": ("name", "text", "The applicant's first (given) name"),
    "last_name": ("name", "text", "The applicant's last (family) name"),
    "email": ("email", "text", "The applicant's email address for this application"),
    "phone": ("phone", "text", "The applicant's phone number"),
    "location": ("location", "text", "Where the applicant currently lives"),
    "linkedin_url": ("link", "text", "The applicant's LinkedIn profile URL"),
    "github_url": ("link", "text", "The applicant's GitHub profile URL"),
    "portfolio_url": ("link", "text", "The applicant's portfolio or personal website URL"),
    "years_of_experience": ("total_experience_years", "number",
                            "The applicant's total years of professional work experience"),
}


def _profile_facts(profile: Any, account_email: str | None) -> list[Fact]:
    scalars = scalar_values(profile)
    if account_email:
        scalars["email"] = account_email
    out = [Fact(id=f"profile.{name}", kind=kind, data_type=data_type, value=scalars[name], description=desc,
                source="profile")
           for name, (kind, data_type, desc) in _PROFILE_FIELDS.items() if scalars.get(name)]
    skills = [str(s).strip() for s in getattr(profile, "skills", None) or [] if str(s).strip()]
    if skills:
        out.append(Fact(id="profile.skills", kind="skills", data_type="list", value=skills, source="profile",
                        description="Technologies and skills the applicant has professional experience with"))
    bio = " ".join((getattr(profile, "bio", None) or "").split())
    if bio:
        out.append(Fact(id="profile.bio", kind="bio", value=bio, source="profile",
                        description="The applicant's short professional bio"))
    for i, e in enumerate(_experience(profile)):
        out.append(Fact(id=f"profile.work_experience[{i}]", kind="work_experience", value=_experience_line(e),
                        source="profile",
                        description=f"Work experience: {e.get('title', '')} at {e.get('company', '')} "
                                    f"({e.get('duration', '')})"))
    for i, e in enumerate(_education(profile)):
        degree = e.get("degree_type") or e.get("degree") or ""
        discipline = f" in {e['discipline']}" if e.get("discipline") else ""
        school = e.get("school") or e.get("institution") or ""
        out.append(Fact(id=f"profile.education[{i}]", kind="education", value=_education_line(e), source="profile",
                        description=f"Education: {degree}{discipline}, {school}"))
    return out


def load_facts(profile: Any, *, account_email: str | None = None) -> FactCatalog:
    """Every fact an answer may rest on: personal.yaml's facts, then the profile's. account_email (the
    run's alias) replaces the profile email. Raises FactsConfigError for an invalid facts: list."""
    facts, placeholders = _yaml_facts()
    if placeholders:
        logger.warning(f"[facts] placeholder values ignored: {', '.join(placeholders)}")
    catalog = {f.id: f for f in facts}
    catalog.update((f.id, f) for f in _profile_facts(profile, account_email))
    return FactCatalog(facts=catalog, placeholders=tuple(placeholders))


def yaml_fact_value(kind: str) -> str | None:
    """The value of the first personal.yaml fact of this kind, as text (None if there is none, or the
    facts: list is invalid)."""
    try:
        facts, _ = _yaml_facts()
    except FactsConfigError as exc:
        logger.warning(f"[facts] {exc}")
        return None
    return next((value_text(f.value) for f in facts if f.kind == kind), None)
