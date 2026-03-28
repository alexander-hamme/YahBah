"""
Maps extracted form fields to applicant profile values using the LLM.

The LLM receives the form schema, profile, and a list of known-answer keys.
It maps each field to either a profile key or a known-answer key.
We resolve values at the end — one lookup, no substring matching.
"""
from pydantic import BaseModel, Field

from yahbah.config import settings
from yahbah.db.models import ApplicantProfile
from yahbah.llm.client import LLMError, OllamaClient
from yahbah.schemas import FieldMapping, FieldMappingResult, FormSchema


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


# ---------------------------------------------------------------------------
# Known answers for policy-driven or sensitive questions.
#
# HOW TO ADD A NEW ONE:
#   1. Pick a short snake_case key, e.g. "work_authorization"
#   2. Add it here with its answer value
#   3. That's it — the LLM is automatically told about the key and will use it
#
# The LLM decides whether a field maps to one of these keys based on semantics.
# We never do substring matching on field labels.
# ---------------------------------------------------------------------------
KNOWN_ANSWERS: dict[str, str] = {
    "salary_expectation": (
        "My base salary expectation is $150K and up, pending further negotiation "
        "to find a mutually satisfactory total compensation package."
    ),
    # TODO: willingness to relocate / areas willing to work
}


def _build_system_prompt() -> str:
    known_keys_block = "\n".join(
        f'  - "{key}": use this for questions about {key.replace("_", " ")}'
        for key in KNOWN_ANSWERS
    )
    return f"""\
You are a form-filling assistant. Given a list of form fields and an applicant profile,
map each field to the correct profile key or known-answer key.

Available known-answer keys (use these when the field matches semantically):
{known_keys_block}

Available profile keys:
  full_name, first_name, last_name, email, phone, location,
  linkedin_url, github_url, resume_path, resume, cover_letter,
  years_of_experience, skills, bio

Rules:
- Prefer known-answer keys when the field matches semantically (e.g. a salary
  question → "salary_expectation"). Do NOT invent values for these — just set
  `mapped_to` to the key and value to "".
- For all other fields, use the matching profile key and fill value from the profile.
- Set confidence = 1.0 when the mapping is obvious, < 0.7 when unsure.
- For file uploads (resume, cover letter) set `mapped_to` to "resume" or "cover_letter".
- If a field truly cannot be mapped, set `mapped_to` to null.
- Name fields: use first_name / last_name when the form has separate fields.
- Respond ONLY with valid JSON:
  {{
    "field_mappings": [
      {{
        "form_label": "<label>",
        "mapped_to": "<key or null>",
        "value": "<value or empty string>",
        "confidence": <0.0–1.0>
      }}
    ]
  }}
"""


_FALLBACK_SYSTEM_PROMPT = """\
You are filling out a job application on behalf of the applicant below.
Answer the following required form question as the applicant would.
Be concise and professional. Reply ONLY with valid JSON: {"answer": "<your answer>"}
"""


class FieldMapper:
    def __init__(self) -> None:
        self._client = OllamaClient()

    async def map(
        self,
        form_schema: FormSchema,
        profile: ApplicantProfile,
        job_description: str = "",
    ) -> FieldMappingResult:
        fields_text = "\n".join(
            f"- label={f.label!r} type={f.field_type}"
            + (f" options={f.options}" if f.options else "")
            + (" [REQUIRED]" if f.required else "")
            for f in form_schema.fields
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

            # Substitute known-answer value; LLM sends value="" for these
            value = KNOWN_ANSWERS.get(item.mapped_to, item.value)

            # Confidence gate for required fields (known-answers always pass)
            if (
                item.form_label in required_labels
                and item.confidence < settings.min_field_confidence
                and item.mapped_to not in KNOWN_ANSWERS
                and item.mapped_to not in ("resume", "cover_letter")
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
            answer = await self._fallback_answer(field.label, profile_text, job_description)
            mappings.append(FieldMapping(
                form_label=field.label,
                mapped_to="llm_fallback",
                value=answer,
                confidence=0.75,
            ))

        return FieldMappingResult(field_mappings=mappings)

    async def _fallback_answer(
        self, field_label: str, profile_text: str, job_description: str
    ) -> str:
        user_prompt = (
            f"QUESTION: {field_label}\n\n"
            f"APPLICANT PROFILE:\n{profile_text}\n\n"
            f"JOB DESCRIPTION (excerpt):\n{job_description[:1500]}"
        )
        result = await self._client.generate_structured(
            system_prompt=_FALLBACK_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=_FallbackAnswer,
        )
        return result.answer
