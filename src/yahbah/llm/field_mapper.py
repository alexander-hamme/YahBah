"""
Maps extracted form fields to applicant profile values using the LLM.

The LLM receives the form schema and profile, and returns a structured
JSON mapping. We validate schema and reject low-confidence required fields.
"""
from pydantic import BaseModel, Field

from yahbah.config import settings
from yahbah.db.models import ApplicantProfile
from yahbah.llm.client import LLMError, OllamaClient
from yahbah.schemas import FieldMapping, FieldMappingResult, FormSchema


# ── Pydantic models for LLM response validation ──────────────────────────────

class _FieldMappingItem(BaseModel):
    form_label: str
    mapped_to: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)


class _FieldMappingResponse(BaseModel):
    field_mappings: list[_FieldMappingItem]


_SYSTEM_PROMPT = """\
You are a form-filling assistant. Given a list of form fields and an applicant profile,
map each field to the correct profile value.

Rules:
- Only use values from the provided profile. Do not invent data.
- Set confidence = 1.0 when the mapping is obvious (e.g. "Email" → email).
- Set confidence < 0.7 when you are unsure. These fields will be skipped.
- For file upload fields (resume, cover letter), set value to the field type name:
  "resume" or "cover_letter".
- Respond ONLY with valid JSON matching this schema exactly:
  {
    "field_mappings": [
      {
        "form_label": "<field label>",
        "mapped_to": "<profile key or field type>",
        "value": "<value to fill>",
        "confidence": <0.0 to 1.0>
      }
    ]
  }
"""


class FieldMapper:
    def __init__(self) -> None:
        self._client = OllamaClient()

    async def map(
        self, form_schema: FormSchema, profile: ApplicantProfile
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

        profile_text = f"""
full_name: {profile.full_name}
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
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=_FieldMappingResponse,
        )

        # Validate required fields have sufficient confidence
        self._validate_confidence(response, form_schema)

        mappings = [
            FieldMapping(
                form_label=item.form_label,
                mapped_to=item.mapped_to,
                value=item.value,
                confidence=item.confidence,
            )
            for item in response.field_mappings
        ]
        return FieldMappingResult(field_mappings=mappings)

    def _validate_confidence(
        self,
        response: _FieldMappingResponse,
        form_schema: FormSchema,
    ) -> None:
        required_labels = {
            f.label for f in form_schema.fields if f.required
        }
        for item in response.field_mappings:
            if (
                item.form_label in required_labels
                and item.confidence < settings.min_field_confidence
                and item.mapped_to not in ("resume", "cover_letter")
            ):
                raise LLMError(
                    f"Required field '{item.form_label}' has confidence "
                    f"{item.confidence:.2f} < {settings.min_field_confidence}. "
                    "Refusing to proceed to protect application integrity."
                )
