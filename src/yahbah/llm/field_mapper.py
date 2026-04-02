"""
Maps extracted form fields to applicant profile values using the LLM.

The LLM receives the form schema, profile, and a list of known-answer keys.
It maps each field to either a profile key or a known-answer key.
We resolve values at the end — one lookup, no substring matching.
"""
from pydantic import BaseModel, Field
from sqlalchemy import inspect as sa_inspect

from yahbah.config import settings, load_prompts_config
from yahbah.db.models import ApplicantProfile
from yahbah.llm.client import LLMError, OllamaClient
from yahbah.schemas import FieldMapping, FieldMappingResult, FormSchema, FormField

# ── Profile key derivation ────────────────────────────────────────────────────
# Infrastructure columns on ApplicantProfile that are never form-fill targets.
_EXCLUDED_PROFILE_COLUMNS = {"id", "created_at", "updated_at"}

# Keys derived from profile data but not stored as their own DB columns.
_DERIVED_PROFILE_KEYS = {"first_name", "last_name"}

# Special sentinels for file-upload fields — not profile columns.
_UPLOAD_KEYS = {"resume", "cover_letter"}


def _profile_keys() -> list[str]:
    """
    Returns the full list of valid mapped_to keys for the LLM prompt,
    derived directly from ApplicantProfile's mapped columns.
    Derived and upload keys are appended after the DB columns.
    """
    cols = [
        c.key
        for c in sa_inspect(ApplicantProfile).mapper.columns
        if c.key not in _EXCLUDED_PROFILE_COLUMNS
    ]
    return cols + sorted(_DERIVED_PROFILE_KEYS) + sorted(_UPLOAD_KEYS)


# ── Pydantic models for LLM response validation ──────────────────────────────

class _FieldMappingItem(BaseModel):
    form_label: str
    mapped_to: str | None
    value: str
    confidence: float = Field(ge=0.0, le=1.0)


class _FieldMappingResponse(BaseModel):
    field_mappings: list[_FieldMappingItem]


class _FallbackAnswer(BaseModel):
    answer: str


def _known_answers() -> dict[str, str]:
    """Load known answers from config/prompts.yaml."""
    return load_prompts_config()["known_answers"]


def _build_system_prompt() -> str:
    known = _known_answers()
    known_keys_block = "\n".join(
        f'  - "{key}": use this for questions about {key.replace("_", " ")}'
        for key in known
    )
    profile_keys_block = ", ".join(_profile_keys())
    template = load_prompts_config()["prompts"]["field_mapper"]
    return template.format(
        known_keys_block=known_keys_block,
        profile_keys_block=profile_keys_block,
    )




class FieldMapper:
    def __init__(self) -> None:
        self._client = OllamaClient()

    async def map(
        self,
        form_schema: FormSchema,
        profile: ApplicantProfile,
        job_description: str = "",
    ) -> FieldMappingResult:
        def _field_line(f: "FormField", idx: int) -> str:
            parts = [f"[{idx}] label={f.label!r} type={f.field_type}"]
            if f.element_id:
                parts.append(f"id={f.element_id!r}")
            if f.name:
                parts.append(f"name={f.name!r}")
            if f.options:
                parts.append(f"options={f.options}")
            if f.required:
                parts.append("[REQUIRED]")
            return " ".join(parts)

        fields_text = "\n".join(
            _field_line(f, i) for i, f in enumerate(form_schema.fields)
        )

        edu_lines = "\n".join(
            "  - {degree} from {institution} ({year}){gpa}".format(
                degree=e.get("degree", ""),
                institution=e.get("institution", ""),
                year=e.get("year", ""),
                gpa=f", GPA: {e['gpa']}" if e.get("gpa") else "",
            )
            for e in profile.education
        )

        name_parts = profile.full_name.split(maxsplit=1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

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

        profile_text = f"""
full_name: {profile.full_name}
first_name: {first_name}
last_name: {last_name}
email: {profile.email}
phone: {profile.phone}
location: {profile.location}
linkedin_url: {profile.linkedin_url or 'N/A'}
github_url: {profile.github_url or 'N/A'}
resume_path: {profile.resume_path}
years_of_experience: {profile.years_of_experience}
skills: {', '.join(profile.skills)}
bio: {profile.bio or 'N/A'}
work_experience:
{exp_lines}
education:
{edu_lines}
"""

        user_prompt = f"FORM FIELDS:\n{fields_text}\n\nAPPLICANT PROFILE:\n{profile_text}"

        response = await self._client.generate_structured(
            system_prompt=_build_system_prompt(),
            user_prompt=user_prompt,
            response_model=_FieldMappingResponse,
        )

        # Resolve values and handle unmapped required fields
        required_labels = {f.label for f in form_schema.fields if f.required}
        mapped_labels = {item.form_label for item in response.field_mappings if item.mapped_to}
        unmapped_required = [
            f for f in form_schema.fields
            if f.required and f.label not in mapped_labels
        ]

        mappings: list[FieldMapping] = []

        for item in response.field_mappings:
            if item.mapped_to is None:
                continue

            # Resolve value:
            #   resume         → path from profile
            #   known answers  → hardcoded value from KNOWN_ANSWERS
            #   custom_question → generate a tailored answer using full profile
            #   everything else → LLM's own value string
            if item.mapped_to == "resume":
                value = profile.resume_path
            elif item.mapped_to == "custom_question":
                value = await self._fallback_answer(item.form_label, profile_text, job_description, mappings)
            else:
                value = _known_answers().get(item.mapped_to, item.value)

            # Confidence gate for required fields (known-answers always pass)
            if (
                item.form_label in required_labels
                and item.confidence < settings.min_field_confidence
                and item.mapped_to not in _known_answers()
                and item.mapped_to not in ("resume", "cover_letter", "custom_question")
            ):
                raise LLMError(
                    f"Required field '{item.form_label}' has confidence "
                    f"{item.confidence:.2f} < {settings.min_field_confidence}."
                )

            mappings.append(FieldMapping(
                form_label=item.form_label,
                mapped_to=item.mapped_to,
                value=value,
                confidence=item.confidence,
            ))

        # Fallback: LLM-generate answers for required fields still unmapped
        for field in unmapped_required:
            answer = await self._fallback_answer(field.label, profile_text, job_description, mappings)
            mappings.append(FieldMapping(
                form_label=field.label,
                mapped_to="llm_fallback",
                value=answer,
                confidence=0.75,
            ))

        # Deduplicate by form_label — keep the first (highest-intent) mapping
        seen: set[str] = set()
        deduped: list[FieldMapping] = []
        for m in mappings:
            key = m.form_label.strip().lower()
            if key not in seen:
                seen.add(key)
                deduped.append(m)

        return FieldMappingResult(field_mappings=deduped)

    async def _fallback_answer(
        self,
        field_label: str,
        profile_text: str,
        job_description: str,
        prior_mappings: list[FieldMapping] | None = None,
    ) -> str:
        # Include recent prior answers so the LLM has context about what
        # was already answered (e.g. "No" to a parent yes/no question means
        # the follow-up "If yes, explain" should be "N/A").
        prior_context = ""
        if prior_mappings:
            recent = prior_mappings[-5:]  # last 5 for context
            lines = [f"  {m.form_label}: {m.value}" for m in recent if m.value]
            if lines:
                prior_context = f"\nRECENT ANSWERS ON THIS FORM:\n" + "\n".join(lines) + "\n"

        user_prompt = (
            f"QUESTION: {field_label}\n"
            f"{prior_context}\n"
            f"APPLICANT PROFILE:\n{profile_text}\n\n"
            f"JOB DESCRIPTION (excerpt):\n{job_description[:1500]}"
        )
        fallback_prompt = load_prompts_config()["prompts"]["fallback_answer"]
        result = await self._client.generate_structured(
            system_prompt=fallback_prompt,
            user_prompt=user_prompt,
            response_model=_FallbackAnswer,
        )
        return result.answer
