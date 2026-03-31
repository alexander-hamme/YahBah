"""
Maps extracted form fields to applicant profile values using the LLM.

The LLM receives the form schema, profile, and a list of known-answer keys.
It maps each field to either a profile key or a known-answer key.
We resolve values at the end — one lookup, no substring matching.
"""
from pydantic import BaseModel, Field
from sqlalchemy import inspect as sa_inspect

from yahbah.config import settings
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
    # Compensation
    "salary_expectation": (
        # “Based on market rates for senior ML roles, I’d expect total compensation in the $180K–$240K range, but I’m flexible depending on the role, team, and overall package.”
        # “I’m targeting total compensation roughly in the $170K–$250K range, depending on scope and overall package.”
        "Targeting $175K–$240K total compensation (flexible)"
    ),
    "currently_in_location": "(IF LOCATION IS CLOSE TO NYC, Boston, or Philly: "
                             "say 'Yes, willing to relocate'. OTHERWISE, SAY 'Not currently'))",
    "willing_to_relocate": "Yes, willing to relocate",
    # Work authorization
    "country_phone_code": "+1",
    "country_of_residence": "United States",
    "phone_number": "646-820-5134",
    "work_authorization": "Yes",
    "sponsorship_required": "No",
    "work_status": "US Citizen",
    "how_i_found_this_job": "Company Job Board",
    # Voluntary self-identification (EEO) — decline all by default
    "gender_identity":  "Male",
    "sexual_orientation": "Decline to self-identify",
    "transgender_identity": "No",
    "preferred_pronouns": "He/Him",
    "disability_status": "No",
    "veteran_status": "Not a veteran / no military service",
    "race_ethnicity": "Decline to self-identify",
    "hispanic_or_latino": "No",
    "agree_to_allow_info_use": "I agree",
    # Consent / terms checkboxes — always agree
    "consent_checkbox": "true",
}


def _build_system_prompt() -> str:
    known_keys_block = "\n".join(
        f'  - "{key}": use this for questions about {key.replace("_", " ")}'
        for key in KNOWN_ANSWERS
    )
    profile_keys_block = ", ".join(_profile_keys())
    return f"""\
You are a form-filling assistant. Given a list of form fields and an applicant profile,
map each field to the correct profile key or known-answer key.

Available known-answer keys (use these when the field matches semantically):
{known_keys_block}

Available profile keys:
  {profile_keys_block}

Rules:
- Prefer known-answer keys when the field matches semantically (e.g. a salary
  question → "salary_expectation"). Do NOT invent values for these — just set
  `mapped_to` to the key and value to "".
- For open-ended experience/qualification questions — e.g. "describe your experience
  with X", "explain your background in Y", "briefly explain your interaction with Z"
  — set `mapped_to` to "custom_question" and value to "". A dedicated step will
  generate a tailored answer using the full profile and job description. Use this
  whenever the question asks the applicant to explain, describe, or elaborate on
  something specific to the role or their background. Do NOT map these to "bio".
- For all other standard fields, use the matching profile key and fill value from the profile.
- Set confidence = 1.0 when the mapping is obvious, < 0.7 when unsure.
- For file upload fields (type=file): use the label, element id, and name together
  to determine intent. Set `mapped_to` to "resume" if it is for a CV/resume,
  "cover_letter" if it is for a cover letter, or null if uncertain.
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
Answer the following form question as the applicant would, in first person.

Guidelines:
- Be concise and professional (1-3 sentences unless the question asks for more).
- For experience or skill questions: draw directly from the work experience bullets
  and skills list. Give concrete, specific examples (purpose of product, technologies used,
  (outcome if helpful/relevant), rather than generic statements. Convey strong familiarity in an industry/production-level setting.
- Do NOT mention applying for a job or reference the company by name unless asked.

Reply ONLY with valid JSON: {"answer": "<your answer>"}
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
                value = await self._fallback_answer(item.form_label, profile_text, job_description)
            else:
                value = KNOWN_ANSWERS.get(item.mapped_to, item.value)

            # Confidence gate for required fields (known-answers always pass)
            if (
                item.form_label in required_labels
                and item.confidence < settings.min_field_confidence
                and item.mapped_to not in KNOWN_ANSWERS
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
