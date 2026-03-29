"""
Shared domain types used across workflow, browser, and LLM layers.
All types must be JSON-serializable (Temporal requirement).
"""
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Browser / Form extraction
# ---------------------------------------------------------------------------

@dataclass
class FormField:
    label: str
    field_type: str          # text | email | tel | select | file | textarea | checkbox
    name: str | None = None  # HTML name attribute
    element_id: str | None = None
    placeholder: str | None = None
    required: bool = False
    options: list[str] = field(default_factory=list)  # for select fields


@dataclass
class FormSchema:
    fields: list[FormField]
    page_url: str
    page_title: str


# ---------------------------------------------------------------------------
# LLM field mapping
# ---------------------------------------------------------------------------

@dataclass
class FieldMapping:
    form_label: str
    mapped_to: str   # full_name | email | phone | location | linkedin | cover_letter | resume | other
    value: str
    confidence: float


@dataclass
class FieldMappingResult:
    field_mappings: list[FieldMapping]


# ---------------------------------------------------------------------------
# Activity I/O (passed through Temporal — must stay JSON-serializable)
# ---------------------------------------------------------------------------

@dataclass
class BrowserExtractInput:
    run_id: str
    job_url: str


@dataclass
class BrowserExtractOutput:
    form_schema_dict: dict   # FormSchema serialized to dict
    job_description: str


@dataclass
class MapFieldsInput:
    run_id: str
    form_schema_dict: dict   # FormSchema serialized to dict
    job_description: str = ""


@dataclass
class MapFieldsOutput:
    field_mappings: list[dict]  # list[FieldMapping] serialized


@dataclass
class CoverLetterInput:
    run_id: str
    job_description: str


@dataclass
class CoverLetterOutput:
    cover_letter_path: str
    cover_letter_text: str


@dataclass
class BrowserFillInput:
    run_id: str
    job_url: str
    field_mappings: list[dict]
    form_schema_dict: dict        # passed so the filler can use element_id/name/placeholder
    cover_letter_path: str | None  # path to generated PDF
    cover_letter_text: str | None = None  # raw text for textarea cover letter fields


@dataclass
class BrowserFillOutput:
    confirmation_url: str | None
    confirmation_text: str | None


# ---------------------------------------------------------------------------
# Auth / account creation
# ---------------------------------------------------------------------------

@dataclass
class AuthInput:
    run_id: str
    job_url: str


@dataclass
class AuthOutput:
    auth_required: bool
    account_email: str | None
    account_password: str | None
