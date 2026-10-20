"""
Maps form fields (by their data-ja-field key) to applicant values.

Facts are retrieved, narratives are generated, and nothing generated asserts a new fact:

1. Code: fields with an unambiguous HTML autofill token (given-name, email, ...) map to profile keys.
2. One constrained driver call picks a *source* for every other field: a stored-answer key, a
   profile key, resume / cover_letter, custom, or skip (JSON-schema enums: only real keys).
3. Screening questions (llm/screening.py: work authorization and sponsorship per country,
   citizenship, clearance, years per skill, start date, salary, EEO, ...) are answered only from
   typed stored facts: in code where it can, else by one driver call that sees only those facts.
   No fact -> NeedsAnswer for a required field, empty for an optional one. The driver's pick is
   only a preference.
4. Stored-answer and profile keys are resolved in code; a model-written value for a composite
   profile key (skills, work_experience, ...) must be found in that field.
5. `custom` questions go to the prose model with the applicant facts (llm/profile.py: stored
   answers and profile, one `key: value` line each, bullets included) and, as context only, the job
   description. The reply cites `basis` keys (an enum of the real keys; the job description is
   never one) and says whether it is `generated` prose. Checked in code:
   - a factual answer needs a basis, and must be found in what it cites;
   - material questions (yes/no choices, dates, numbers, credentials) are never generated;
   - every number an answer states must appear in the applicant facts;
   - open, behavioural and personality questions are always answered: generated=True, for review.
6. Grounded factual custom answers are cached with their provenance (llm/answer_cache.py).

Every FieldMapping carries `basis` (the keys backing it) and `generated`.
"""
import json
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy import inspect as sa_inspect

from yahbah.config import load_prompts_config, settings
from yahbah.db.models import ApplicantProfile
from yahbah.llm import answer_cache, screening
from yahbah.llm.client import OllamaClient
from yahbah.llm.grounding import drop_sentences, supported, unsupported_numbers
from yahbah.llm.models import spec
from yahbah.llm.profile import facts as all_facts
from yahbah.llm.profile import known_answers, profile_text, scalar_values, stored_answers, stored_text
from yahbah.schemas import FieldMapping, FormField

__all__ = ["FieldMapper", "NeedsAnswer", "profile_text"]

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

# Field types whose answer is always a fact (never generated prose).
_MATERIAL_TYPES = {"date", "number", "email", "tel", "url"}
_CHOICE_TYPES = {"select", "combobox", "radio", "checkbox"}
_CREDENTIAL_RE = re.compile(r"\b(do|did|are|have) you (have|hold|possess|own|got|completed|earned|certified|licensed)\b"
                            r".{0,50}\b(licen[cs]e|certif\w*|degree|diploma|passport|permit|vehicle|car|credential"
                            r"|accreditation|registration)")
# Choice questions that ask for a personality pick rather than a fact (these may be generated).
_PERSONALITY_RE = re.compile(r"\bif you (were|could)\b|\b(kind|type|sort) of .{0,25}\b(are|would) you\b|favou?rite"
                             r"|which .{0,30}\bdescribes? you\b|would you rather|spirit animal|superpower|personality")
_NA = {"n a", "na", "not applicable", "none", "no"}


def _profile_keys() -> list[str]:
    """Valid profile keys for the prompt, derived from ApplicantProfile's mapped columns."""
    cols = [
        c.key
        for c in sa_inspect(ApplicantProfile).mapper.columns
        if c.key not in _EXCLUDED_PROFILE_COLUMNS
    ]
    return cols + sorted(_DERIVED_PROFILE_KEYS)


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


def _require_all(schema: dict) -> None:
    schema["required"] = list(schema.get("properties", {}))


# Every property is required in the schema Ollama decodes with (defaults only serve validation),
# so a reply always states its basis and flags instead of leaving them out.
_REPLY_CONFIG = ConfigDict(json_schema_extra=_require_all)


class _FallbackAnswer(BaseModel):
    """The prose model's answer to a custom question (the `fallback_answer` prompt). Per call the
    answer is constrained to the options and basis to the real fact keys (`_answer_model`)."""
    generated: bool = False        # open-ended prose (motivation, behavioural, personality)
    insufficient_info: bool = False
    answer: str
    basis: list[str] = []          # fact keys the answer relies on
    job_specific: bool = True

    model_config = _REPLY_CONFIG


def _answer_model(keys: list[str], options: list[str], allow_generated: bool) -> type[BaseModel]:
    """_FallbackAnswer with enums; for a fact question without `generated` (it can't claim to be prose)."""
    fields: dict[str, Any] = {
        "generated": (bool, False),
        "insufficient_info": (bool, False),
        "answer": (Literal[tuple(dict.fromkeys(options + [""]))] if options else str, ...),
        "basis": (list[Literal[tuple(keys)]] if keys else list[str], []),
        "job_specific": (bool, True),
    }
    if not allow_generated:
        del fields["generated"]
    return create_model("FallbackAnswer", __config__=_REPLY_CONFIG, **fields)


class _ScreeningAnswer(BaseModel):
    insufficient_info: bool = False
    answer: str
    basis: list[str] = []

    model_config = _REPLY_CONFIG


def _screening_model(keys: list[str], options: list[str]) -> type[BaseModel]:
    fields: dict[str, Any] = {"basis": (list[Literal[tuple(keys)]], [])}
    if options:
        fields["answer"] = (Literal[tuple(dict.fromkeys(options + [""]))], ...)
    return create_model("ScreeningAnswer", __base__=_ScreeningAnswer, **fields)


class _MatchScoreResponse(BaseModel):
    match_score: int
    match_rationale: str


class NeedsAnswer(Exception):
    """A required question needs a fact the applicant hasn't stored. The run fails with needs_answer."""

    def __init__(self, label: str) -> None:
        self.label = label
        super().__init__(f"needs_answer: {label}")


def _build_system_prompt(stored: dict[str, str]) -> str:
    # The stored answers are shown so the model can check one answers the question *as asked*
    # (e.g. "Visa required?" needs the sponsorship answer, not the work-authorization one).
    known_keys_block = "\n".join(
        f'  - "{key}": stored answer {value[:100]!r}'
        for key, value in stored.items()
    )
    template = load_prompts_config()["prompts"]["field_mapper"]
    return template.format(
        known_keys_block=known_keys_block,
        profile_keys_block=", ".join(_profile_keys()),
    )


def _field_json(f: FormField) -> dict:
    """A field as the model sees it: the same shape read_form_schema returns."""
    d: dict = {"key": f.key, "label": f.label, "type": f.field_type, "required": f.required}
    if f.options:
        d["options"] = f.options
        if f.options_total and f.options_total > len(f.options):
            d["options_total"] = f.options_total
    return d


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


def is_material(f: FormField) -> bool:
    """A question whose answer is an applicant fact and must never be generated prose."""
    if f.field_type in _MATERIAL_TYPES or _CREDENTIAL_RE.search(f.label.casefold()):
        return True
    if f.options and screening.is_yes_no_question(f.label, f.options):
        return True
    return f.field_type in _CHOICE_TYPES and bool(f.options) and not _PERSONALITY_RE.search(f.label.casefold())


@dataclass
class _Applicant:
    """Everything one map() call answers from, read once."""
    profile: Any
    stored: dict[str, str]        # stored answers (known answers + education keys)
    facts: dict[str, str]         # every citable key -> value (stored answers + profile fields)
    scalars: dict[str, str]
    typed: list[screening.Fact]
    home: str
    job_scope: str | None
    job_description: str

    def values(self, keys: list[str]) -> list[str]:
        return [self.facts[k] for k in keys if k in self.facts]

    def composite(self, source: str) -> list[str]:
        """The fact keys of a composite profile key (skills, bio, work_experience, education)."""
        return [k for k in self.facts if k == source or k.startswith(f"{source}[")]


@dataclass
class _Answer:
    value: str
    basis: list[str]
    generated: bool = False


def _mapping(f: FormField, source: str, value: str, confidence: float, basis: list[str] | None = None,
             generated: bool = False) -> FieldMapping:
    return FieldMapping(key=f.key, label=f.label, field_type=f.field_type, source=source, value=value,
                        confidence=confidence, basis=list(basis or []), generated=generated)


# ── Mapper ────────────────────────────────────────────────────────────────────

class FieldMapper:
    def __init__(self, cache_namespace: str = answer_cache.LIVE) -> None:
        self._driver = OllamaClient("driver")
        self._prose = OllamaClient("prose")
        self._cache_namespace = cache_namespace

    async def map(
        self,
        fields: list[FormField],
        profile: ApplicantProfile,
        job_description: str = "",
        prior: list[FieldMapping] | None = None,
        *,
        job_location: str | None = None,
    ) -> list[FieldMapping]:
        """One FieldMapping per field, in form order. Raises NeedsAnswer for an unanswerable required field.

        job_location (the posting's location) decides which country an unscoped authorization or
        sponsorship question is about; without it the job description is scanned for one."""
        stored = stored_answers(profile)
        scalars = scalar_values(profile)
        applicant = _Applicant(
            profile=profile, stored=stored, facts=all_facts(profile, stored), scalars=scalars,
            typed=screening.typed_facts(stored, scalars), home=screening.home_jurisdiction(stored),
            job_scope=screening.job_jurisdiction(job_location, job_description), job_description=job_description,
        )

        # Tier 1, code: standard autofill tokens say exactly what a field is for.
        decided = {
            f.key: SimpleNamespace(source=_AUTOFILL_SOURCES[f.autocomplete], value="", confidence=1.0)
            for f in fields if f.autocomplete in _AUTOFILL_SOURCES
        }
        # Tier 2, one structured driver call for the rest, constrained to real keys and valid sources.
        ask = [f for f in fields if f.key not in decided]
        by_label: dict[str, object] = {}
        if ask:
            sources = sorted(set(stored) | set(_profile_keys()) | _SPECIAL_SOURCES)
            response = await self._driver.generate_structured(
                system_prompt=_build_system_prompt(stored),
                user_prompt=(
                    "FORM FIELDS (JSON):\n" + json.dumps([_field_json(f) for f in ask], ensure_ascii=False, indent=1)
                    + f"\n\nAPPLICANT PROFILE:\n{profile_text(profile)}"
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
            mapping = await self._resolve(f, m, applicant, results)
            results.append(mapping)
            out.append(mapping)
        return out

    async def _resolve(self, f: FormField, m, applicant: _Applicant, prior: list[FieldMapping]) -> FieldMapping:
        source = m.source if m else ("custom" if f.required else "skip")
        confidence = m.confidence if m else 0.0

        if f.field_type == "file":
            # Uploads only ever get our documents; a model can't "answer" a file field.
            if source in ("resume", "cover_letter"):
                return _mapping(f, source, "", confidence)
            if f.required:
                raise NeedsAnswer(f.label)
            return _mapping(f, "skip", "", confidence)
        if source == "skip" and not f.required:
            return _mapping(f, "skip", "", confidence)

        kinds = screening.classify(f.label)
        if kinds:
            return await self._screening(f, kinds, source, confidence, applicant)

        if source in ("skip", "resume"):
            source = "custom"  # a required field that applies always gets an answer; no resume in a text box
        elif source == "cover_letter":
            return _mapping(f, source, "", confidence)  # pasted from the generated letter at fill time
        elif source in applicant.stored:
            return _mapping(f, source, applicant.stored[source], confidence, [source])
        elif source not in _SPECIAL_SOURCES:
            low_confidence = f.required and confidence < settings.min_field_confidence
            if value := applicant.scalars.get(source):
                if not low_confidence:
                    return _mapping(f, source, value, confidence, [source])
            elif m and m.value.strip() and not low_confidence:
                # A composite key (skills, work_experience, ...): the model's value must be in that field.
                backing = [k for k in applicant.composite(source) if supported(m.value, [applicant.facts[k]])]
                if backing:
                    return _mapping(f, source, m.value.strip(), confidence, backing)
            source = "custom"  # let the prose model answer it from the applicant facts

        answer = await self._custom_answer(f, applicant, prior)
        if answer is None:
            if f.required:
                raise NeedsAnswer(f.label)
            return _mapping(f, "skip", "", confidence)
        return _mapping(f, "custom", answer.value, confidence, answer.basis, answer.generated)

    # ── Screening facts ───────────────────────────────────────────────────────

    async def _screening(self, f: FormField, kinds: set[str], source: str, confidence: float,
                         applicant: _Applicant) -> FieldMapping:
        res = screening.resolve(f.label, f.field_type, f.options, kinds, applicant.typed,
                                job_scope=applicant.job_scope, home=applicant.home, preferred=source)
        if res.value is not None:
            verbatim = not res.derived and len(res.basis) == 1
            return _mapping(f, res.basis[0] if verbatim else "custom", res.value, confidence, res.basis)
        if res.candidates and (answer := await self._derive(f, res.candidates)):
            return _mapping(f, "custom", answer.value, confidence, answer.basis)
        logger.info(f"[map] {f.label!r} is a screening question ({', '.join(sorted(kinds))}): "
                    f"{res.missing or 'the stored facts do not answer it'}")
        if f.required:
            raise NeedsAnswer(f.label)
        return _mapping(f, "skip", "", confidence)

    async def _derive(self, f: FormField, candidates: list[screening.Fact]) -> _Answer | None:
        """One driver call that may use only the candidate facts."""
        lines = "\n".join(f"{c.key}: {c.value}" for c in candidates)
        options = f"OPTIONS: {f.options}\n" if f.options else ""
        reply = await self._driver.generate_structured(
            system_prompt=load_prompts_config()["prompts"]["screening_answer"],
            user_prompt=f"QUESTION: {f.label}\n{options}\nFACTS:\n{lines}",
            response_model=_screening_model([c.key for c in candidates], f.options),
        )
        value = reply.answer.strip()
        cited = [c for c in candidates if c.key in reply.basis]
        if reply.insufficient_info or not value or not cited:
            return None
        if not f.options and unsupported_numbers(value, *(c.value for c in cited)):
            logger.warning(f"[map] screening answer {value!r} to {f.label!r} states numbers its facts don't")
            return None
        return _Answer(value, [c.key for c in cited])

    # ── Custom questions ──────────────────────────────────────────────────────

    async def _custom_answer(self, f: FormField, applicant: _Applicant, prior: list[FieldMapping]) -> _Answer | None:
        material = is_material(f)
        allow_generated = not material
        prompt = load_prompts_config()["prompts"]["fallback_answer"]
        provenance = dict(facts=applicant.facts, stored_keys=list(applicant.stored), model=spec("prose").model,
                          prompt_hash=answer_cache.text_hash(prompt))
        cacheable = answer_cache.cacheable(f.label, applicant.job_description)
        if cacheable and (hit := await answer_cache.lookup(self._cache_namespace, f.label, f.options, **provenance)):
            return _Answer(*hit)

        reply = await self._ask_prose(f, applicant, prior, prompt, allow_generated)
        answer = self._check(f, reply, applicant, prior, material)
        if answer and answer.generated:
            answer = await self._ground_numbers(f, answer, applicant, prior, prompt)
        if answer and cacheable and not answer.generated and not reply.job_specific:
            await answer_cache.store(self._cache_namespace, f.label, f.options, answer.value, answer.basis,
                                     **provenance)
        return answer

    async def _ask_prose(self, f: FormField, applicant: _Applicant, prior: list[FieldMapping], prompt: str,
                         allow_generated: bool, feedback: str = "") -> _FallbackAnswer:
        # Recent answers give context for conditional follow-ups ("If yes, please explain").
        recent = [f"  {m.label}: {m.value}" for m in prior[-5:] if m.value and m.source != "resume"]
        prior_context = "RECENT ANSWERS ON THIS FORM:\n" + "\n".join(recent) + "\n\n" if recent else ""
        options = f"OPTIONS: {f.options}\n" if f.options else ""
        kind = ("" if allow_generated else
                "This is a FACT question: answer it only from the APPLICANT FACTS, or set insufficient_info.\n")
        job = (f"\n\nJOB (context only, never evidence about the applicant):\n{applicant.job_description[:6000]}"
               if allow_generated and applicant.job_description.strip() else "")
        user_prompt = (f"QUESTION: {f.label}\nFIELD TYPE: {f.field_type}\n{options}{kind}{feedback}\n"
                       f"{prior_context}APPLICANT FACTS (cite these keys in basis):\n"
                       f"{stored_text(applicant.stored)}\n{profile_text(applicant.profile)}{job}")
        return await self._prose.generate_structured(
            system_prompt=prompt, user_prompt=user_prompt,
            response_model=_answer_model(list(applicant.facts), f.options, allow_generated),
        )

    def _check(self, f: FormField, reply: _FallbackAnswer, applicant: _Applicant, prior: list[FieldMapping],
               material: bool) -> _Answer | None:
        """Accept a prose reply only as a backed fact or as labelled generated prose."""
        value = reply.answer.strip()
        if reply.insufficient_info or not value:
            return None
        basis = [k for k in dict.fromkeys(reply.basis) if k in applicant.facts]
        generated = bool(getattr(reply, "generated", False)) and not material

        if _norm(value) in _NA and answer_cache.is_conditional(f.label):
            # A follow-up to a question answered "No": backed by what backs that answer.
            parent = next((m for m in reversed(prior) if _norm(m.value) in _NA or screening.yes_no(m.value) == "no"),
                          None)
            basis = basis or (parent.basis if parent else [])
            return _Answer(value, basis) if basis else None
        copied = [v for v in applicant.values(basis) if _norm(value) == _norm(v)
                  or (len(_norm(value).split()) >= 2 and _norm(value) in _norm(v))]
        if generated and copied:
            generated = False  # a value copied from what it cites ("Acme Data") is a retrieved fact
        if not generated:
            if not basis:
                if material:
                    logger.warning(f"[map] {f.label!r}: factual answer {value!r} cites no stored fact: not used")
                    return None
                generated = True  # model-written text that claims no fact: labelled for review
            elif not (screening.yes_no(value) or supported(value, applicant.values(basis))):
                logger.warning(f"[map] {f.label!r}: answer {value!r} is not in what it cites ({basis}): not used")
                return None
            elif extra := unsupported_numbers(value, f.label, *applicant.values(basis)):
                logger.warning(f"[map] {f.label!r}: answer {value!r} states numbers its basis doesn't ({extra})")
                return None
        return _Answer(value, basis, generated)

    async def _ground_numbers(self, f: FormField, answer: _Answer, applicant: _Applicant,
                              prior: list[FieldMapping], prompt: str) -> _Answer | None:
        """Generated prose may not introduce numbers: one rewrite, then the offending sentences go."""
        material_text = [f.label, *applicant.facts.values(), *(f.options or [])]
        extra = unsupported_numbers(answer.value, *material_text)
        if not extra:
            return answer
        logger.warning(f"[map] {f.label!r}: generated answer states numbers not in the applicant facts ({extra})")
        feedback = (f"Your previous answer stated numbers that are not in the APPLICANT FACTS "
                    f"({', '.join(sorted(extra))}). Write it again without any number the facts don't contain.\n")
        retry = await self._ask_prose(f, applicant, prior, prompt, True, feedback)
        value = retry.answer.strip() if not retry.insufficient_info else ""
        extra = unsupported_numbers(value, *material_text)
        if value and extra:
            value = drop_sentences(value, extra)
        if not value:
            value = drop_sentences(answer.value, unsupported_numbers(answer.value, *material_text))
        return _Answer(value, answer.basis, True) if value else None

    async def compute_match_score(self, profile_summary: str, job_description: str) -> tuple[int, str]:
        """Score how well the applicant matches the job (22-99%)."""
        salary_info = known_answers().get("salary_expectation", "Not specified")
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
