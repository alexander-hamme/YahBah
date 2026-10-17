"""
Maps form fields (by their data-ja-field key) to applicant values.

Code first: fields carrying an unambiguous HTML autofill token (given-name, email, ...) are mapped
without a model. One driver call decides the *source* of every other field: a profile key, a
known-answer key, resume / cover_letter, custom, or skip — the reply schema only allows the real
field keys and valid sources (JSON-schema enums Ollama enforces while decoding). Values are then
resolved in code from ApplicantProfile and personal.yaml known_answers — no model-invented facts.
Custom questions are answered by the prose model with the full profile and job description;
answers that don't depend on the specific job are cached (answer_cache).
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field, create_model
from sqlalchemy import inspect as sa_inspect

from yahbah.config import load_prompts_config, settings
from yahbah.db.models import AnswerCache, ApplicantProfile
from yahbah.db.session import AsyncSessionLocal
from yahbah.llm.client import OllamaClient
from yahbah.schemas import FieldMapping, FormField

# ── Profile key derivation ────────────────────────────────────────────────────
# Columns on ApplicantProfile that are never form values (the resume is the "resume" source).
_EXCLUDED_PROFILE_COLUMNS = {"id", "created_at", "updated_at", "resume_path"}

# Unambiguous HTML autofill tokens (WHATWG autofill field names) -> profile keys: mapped in code.
_AUTOFILL_SOURCES = {
    "given-name": "first_name",
    "family-name": "last_name",
    "name": "full_name",
    "email": "email",
    "tel": "phone",
    "tel-national": "phone",
}

# Keys derived from profile data but not stored as their own DB columns.
_DERIVED_PROFILE_KEYS = {"first_name", "last_name"}

# Sources that aren't profile or known-answer keys.
_SPECIAL_SOURCES = {"resume", "cover_letter", "custom", "skip"}


def _profile_keys() -> list[str]:
    """Valid profile keys for the prompt, derived from ApplicantProfile's mapped columns."""
    cols = [
        c.key
        for c in sa_inspect(ApplicantProfile).mapper.columns
        if c.key not in _EXCLUDED_PROFILE_COLUMNS
    ]
    return cols + sorted(_DERIVED_PROFILE_KEYS)


def _profile_value(profile: ApplicantProfile, key: str) -> str | None:
    """Values resolved in code for simple profile keys (others use the model's formatted value)."""
    first, _, last = profile.full_name.partition(" ")
    simple = {
        "full_name": profile.full_name,
        "first_name": first,
        "last_name": last,
        "email": profile.email,
        "phone": profile.phone,
        "location": profile.location,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
        "years_of_experience": str(profile.years_of_experience),
    }
    return simple.get(key)


# ── Pydantic models for LLM response validation ──────────────────────────────

class _KeyMapping(BaseModel):
    key: str
    source: str
    value: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class _MappingResponse(BaseModel):
    mappings: list[_KeyMapping]


def _response_model(keys: list[str], sources: list[str]) -> type[BaseModel]:
    """_MappingResponse with key and source constrained to the valid values (JSON-schema enums,
    enforced by Ollama while decoding): the model can't echo labels as keys or invent sources."""
    item = create_model(
        "KeyMapping",
        key=(Literal[tuple(keys)], ...),
        source=(Literal[tuple(sources)], ...),
        value=(str, ""),
        confidence=(float, Field(ge=0.0, le=1.0)),
    )
    return create_model("MappingResponse", mappings=(list[item], ...))


class _FallbackAnswer(BaseModel):
    answer: str
    # Verbatim quotes from the material given (stored answers, profile, recent answers, job description)
    # that the answer relies on; checked in code, so an unbacked answer can't pass as fact.
    evidence: list[str] = []
    insufficient_info: bool = False
    job_specific: bool = True


class _MatchScoreResponse(BaseModel):
    match_score: int
    match_rationale: str


class NeedsAnswer(Exception):
    """A required question needs a fact the profile doesn't have. The run fails with needs_answer."""

    def __init__(self, label: str) -> None:
        self.label = label
        super().__init__(f"needs_answer: {label}")


def _known_answers() -> dict[str, str]:
    """Known answers from config/personal.yaml."""
    return load_prompts_config()["known_answers"]


def _education_answers() -> dict[str, str]:
    """Education known-answer keys from the YAML profile's most recent education entry."""
    education = load_prompts_config().get("profile", {}).get("education", [])
    if not education:
        return {}
    edu = education[0]
    answers: dict[str, str] = {}
    for key in ("school", "degree_type", "discipline",
                "start_month", "start_year", "end_month", "end_year", "gpa"):
        val = edu.get(key)
        if val:
            answers[f"education_{key}"] = str(val)
    return answers


def _all_known_answers() -> dict[str, str]:
    """Base known answers plus education-derived keys."""
    answers = {k: str(v) for k, v in _known_answers().items()}
    answers.update(_education_answers())
    return answers


def _build_system_prompt() -> str:
    # The stored answers are shown so the model can check one answers the question *as asked*
    # (e.g. "Visa required?" needs the sponsorship answer, not the work-authorization one).
    known_keys_block = "\n".join(
        f'  - "{key}": stored answer {value[:100]!r}'
        for key, value in _all_known_answers().items()
    )
    template = load_prompts_config()["prompts"]["field_mapper"]
    return template.format(
        known_keys_block=known_keys_block,
        profile_keys_block=", ".join(_profile_keys()),
    )


def profile_text(profile: ApplicantProfile) -> str:
    """The applicant profile as prompt text."""
    first, _, last = profile.full_name.partition(" ")
    edu_lines = "\n".join(
        "  - {degree_type} in {discipline} from {school} ({end_year}){gpa}".format(
            degree_type=e.get("degree_type", e.get("degree", "")),
            discipline=e.get("discipline", ""),
            school=e.get("school", e.get("institution", "")),
            end_year=e.get("end_year", e.get("year", "")),
            gpa=f", GPA: {e['gpa']}" if e.get("gpa") else "",
        )
        for e in profile.education
    )
    exp_lines = "\n".join(
        "  - {title} at {company}{location} ({duration})".format(
            title=e.get("title", ""),
            company=e.get("company", ""),
            location=f", {e['location']}" if e.get("location") else "",
            duration=e.get("duration", ""),
        )
        for e in profile.work_experience
        if e.get("use_in_custom_prompts", True)
    )
    return f"""
full_name: {profile.full_name}
first_name: {first}
last_name: {last}
email: {profile.email}
phone: {profile.phone}
location: {profile.location}
linkedin_url: {profile.linkedin_url or 'N/A'}
github_url: {profile.github_url or 'N/A'}
portfolio_url: {profile.portfolio_url or 'N/A'}
years_of_experience: {profile.years_of_experience}
skills: {', '.join(profile.skills)}
bio: {profile.bio or 'N/A'}
work_experience:
{exp_lines}
education:
{edu_lines}
"""


def _field_json(f: FormField) -> dict:
    """A field as the model sees it: the same shape read_form_schema returns."""
    d: dict = {"key": f.key, "label": f.label, "type": f.field_type, "required": f.required}
    if f.options:
        d["options"] = f.options
        if f.options_total and f.options_total > len(f.options):
            d["options_total"] = f.options_total
    return d


# ── Answer cache ──────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


def _question_key(label: str, options: list[str]) -> str:
    raw = _norm(label) + "|" + "|".join(sorted(_norm(o) for o in options))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def _cached_answer(label: str, options: list[str]) -> str | None:
    async with AsyncSessionLocal() as session:
        row = await session.get(AnswerCache, _question_key(label, options))
        if row is None:
            return None
        row.hits += 1
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return row.answer


async def _store_answer(label: str, options: list[str], answer: str) -> None:
    key = _question_key(label, options)
    async with AsyncSessionLocal() as session:
        if await session.get(AnswerCache, key) is None:
            session.add(AnswerCache(question_key=key, question=label, options=options, answer=answer))
            await session.commit()


# ── Mapper ────────────────────────────────────────────────────────────────────

class FieldMapper:
    def __init__(self) -> None:
        self._driver = OllamaClient("driver")
        self._prose = OllamaClient("prose")

    async def map(
        self,
        fields: list[FormField],
        profile: ApplicantProfile,
        job_description: str = "",
        prior: list[FieldMapping] | None = None,
    ) -> list[FieldMapping]:
        """One FieldMapping per field, in form order. Raises NeedsAnswer for an unanswerable required field.

        Tiers: HTML autofill tokens (code) -> one constrained driver call for the rest -> the prose
        model for custom questions (with the full profile + job description) -> NeedsAnswer."""
        ptext = profile_text(profile)
        known = _all_known_answers()

        # Tier 1, code: standard autofill tokens say exactly what a field is for.
        decided = {
            f.key: SimpleNamespace(source=_AUTOFILL_SOURCES[f.autocomplete], value="", confidence=1.0)
            for f in fields if f.autocomplete in _AUTOFILL_SOURCES
        }
        # Tier 2, one structured driver call for the rest, constrained to real keys and valid sources.
        ask = [f for f in fields if f.key not in decided]
        by_label: dict[str, object] = {}
        if ask:
            sources = sorted(set(known) | set(_profile_keys()) | _SPECIAL_SOURCES)
            response = await self._driver.generate_structured(
                system_prompt=_build_system_prompt(),
                user_prompt=(
                    "FORM FIELDS (JSON):\n" + json.dumps([_field_json(f) for f in ask], ensure_ascii=False, indent=1)
                    + f"\n\nAPPLICANT PROFILE:\n{ptext}"
                ),
                response_model=_response_model([f.key for f in ask], sources),
            )
            for m in response.mappings:
                decided.setdefault(m.key, m)
                by_label.setdefault(_norm(m.key), m)  # a model that echoed labels as keys

        results: list[FieldMapping] = list(prior or [])
        out: list[FieldMapping] = []

        for f in fields:
            m = decided.get(f.key) or by_label.get(_norm(f.label))
            source = m.source if m else ("custom" if f.required else "skip")
            confidence = m.confidence if m else 0.0
            value = ""

            if f.field_type == "file" and source not in ("resume", "cover_letter"):
                # Uploads only ever get our documents; a model can't "answer" a file field.
                if f.required:
                    raise NeedsAnswer(f.label)
                source = "skip"
            elif source == "skip" and f.required:
                source = "custom"  # a required field that applies always gets an answer
            elif source == "resume" and f.field_type != "file":
                source = "custom"  # we can't paste a resume into a text box
            elif source in known:
                value = known[source]
            elif source not in _SPECIAL_SOURCES:
                value = _profile_value(profile, source) or (m.value if m else "")
                low_confidence = f.required and confidence < settings.min_field_confidence
                if not value or low_confidence:
                    source = "custom"  # let the prose model answer it with the full context

            if source == "custom":
                value = await self._custom_answer(f, ptext, job_description, results)
                if not value:
                    source = "skip"
                confidence = max(confidence, 0.75)

            mapping = FieldMapping(key=f.key, label=f.label, field_type=f.field_type,
                                   source=source, value=value, confidence=confidence)
            results.append(mapping)
            out.append(mapping)
        return out

    async def _custom_answer(
        self,
        f: FormField,
        ptext: str,
        job_description: str,
        prior: list[FieldMapping],
    ) -> str:
        if cached := await _cached_answer(f.label, f.options):
            return cached

        # Recent answers give context for conditional follow-ups ("If yes, please explain").
        recent = [f"  {m.label}: {m.value}" for m in prior[-5:] if m.value and m.source != "resume"]
        prior_context = "\nRECENT ANSWERS ON THIS FORM:\n" + "\n".join(recent) + "\n" if recent else ""
        options = f"OPTIONS: {f.options}\n" if f.options else ""
        stored = "\n".join(f"  {k}: {v}" for k, v in _all_known_answers().items())
        material = (f"APPLICANT'S STORED ANSWERS:\n{stored}\n\n"
                    f"APPLICANT PROFILE:\n{ptext}\n\n"
                    f"JOB DESCRIPTION:\n{job_description}")
        result = await self._prose.generate_structured(
            system_prompt=load_prompts_config()["prompts"]["fallback_answer"],
            user_prompt=f"QUESTION: {f.label}\n{options}{prior_context}\n{material}",
            response_model=_FallbackAnswer,
        )
        # Code-side honesty check: at least one quote must really occur in what the model was given.
        haystack = _norm(material + prior_context)
        backed = [q for q in result.evidence if len(_norm(q)) >= 2 and _norm(q) in haystack]
        unbacked = result.answer.strip() and not result.insufficient_info and not backed
        if unbacked:
            logger.warning(f"[map] answer to {f.label!r} has no verifiable evidence "
                           f"(quotes: {result.evidence!r}): treated as a guess")
        if result.insufficient_info or not result.answer.strip() or unbacked:
            if f.required:
                raise NeedsAnswer(f.label)
            return ""
        if not result.job_specific:
            await _store_answer(f.label, f.options, result.answer)
        return result.answer

    async def compute_match_score(self, profile_summary: str, job_description: str) -> tuple[int, str]:
        """Score how well the applicant matches the job (22-99%)."""
        salary_info = _known_answers().get("salary_expectation", "Not specified")
        user_prompt = (
            f"APPLICANT PROFILE:\n{profile_summary}\n\n"
            f"APPLICANT SALARY EXPECTATION: {salary_info}\n\n"
            f"JOB DESCRIPTION:\n{job_description}"
        )
        result = await self._driver.generate_structured(
            system_prompt=load_prompts_config()["prompts"]["match_score"],
            user_prompt=user_prompt,
            response_model=_MatchScoreResponse,
        )
        return max(22, min(99, result.match_score)), result.match_rationale
