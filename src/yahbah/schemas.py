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
    canonical_url: str = ""
    job_title: str | None = None
    job_company: str | None = None
    job_location: str | None = None
    posted_date: str | None = None


@dataclass
class JobMetadataInput:
    run_id: str
    job_description: str


@dataclass
class JobMetadataOutput:
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description_summary: str | None = None  # ≤40 word LLM summary
    salary_min: int | None = None
    salary_max: int | None = None
    company_website: str | None = None
    industry: str | None = None
    company_description: str | None = None
    work_model: str | None = None
    seniority_level: str | None = None
    experience_required: str | None = None
    posted_date: str | None = None
    technologies: list[str] | None = None
    specialties: list[str] | None = None


@dataclass
class DuplicateCheckInput:
    run_id: str
    canonical_url: str
    job_title: str | None
    job_company: str | None
    job_location: str | None
    job_description: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    company_website: str | None = None
    industry: str | None = None
    company_description: str | None = None
    work_model: str | None = None
    seniority_level: str | None = None
    experience_required: str | None = None
    posted_date: str | None = None
    technologies: list[str] | None = None
    specialties: list[str] | None = None


@dataclass
class DuplicateCheckOutput:
    is_duplicate: bool
    reason: str | None = None
    existing_run_id: str | None = None


@dataclass
class MapFieldsInput:
    run_id: str
    form_schema_dict: dict   # FormSchema serialized to dict
    job_description: str = ""


@dataclass
class MapFieldsOutput:
    field_mappings: list[dict]  # list[FieldMapping] serialized
    match_score: int | None = None
    match_rationale: str | None = None


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
    account_email: str | None = None      # email used for Greenhouse account (needed for verification)


@dataclass
class BrowserFillOutput:
    confirmation_url: str | None
    confirmation_text: str | None
    confirmation_html: str | None
    tracking_url: str | None


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
