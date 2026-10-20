"""
Shared domain types used across workflow, browser, and LLM layers.
All types must be JSON-serializable (Temporal requirement).
"""
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Form schema (from yahbah.agent.discover.map_form)
# ---------------------------------------------------------------------------

@dataclass
class FormField:
    key: str                 # data-ja-field key stamped on the element: how fill finds it again
    label: str
    field_type: str          # text | email | tel | number | url | date | textarea | select | combobox | radio | checkbox | file
    required: bool = False
    options: list[str] = field(default_factory=list)  # select/combobox/radio/checkbox group (≤15 shown)
    options_total: int | None = None                   # when there are more options than shown
    empty_value: str | None = None  # what a custom widget shows before anything is chosen (e.g. "Select...")
    autocomplete: str | None = None  # HTML autofill token, e.g. "given-name", "email"


@dataclass
class FormSchema:
    fields: list[FormField]
    page_url: str
    board: str = "generic"


# ---------------------------------------------------------------------------
# Answer mapping
# ---------------------------------------------------------------------------

@dataclass
class FieldMapping:
    key: str
    label: str
    field_type: str
    source: str      # profile key | known-answer key | resume | cover_letter | custom | skip
    value: str       # resolved value ("" for resume/cover_letter: filled from files at fill time)
    confidence: float
    basis: list[str] = field(default_factory=list)  # stored keys / profile fields backing a factual answer
    generated: bool = False  # model-written prose (open-ended / personality questions), shown in review


# ---------------------------------------------------------------------------
# Activity I/O (passed through Temporal — must stay JSON-serializable)
# ---------------------------------------------------------------------------

@dataclass
class DiscoverInput:
    run_id: str
    job_url: str


@dataclass
class DiscoverOutput:
    page_url: str
    board: str
    account_email: str
    job_description: str
    canonical_url: str = ""
    job_title: str | None = None
    job_company: str | None = None
    job_location: str | None = None
    posted_date: str | None = None


@dataclass
class MapFormInput:
    run_id: str
    job_url: str


@dataclass
class MapFormOutput:
    form_schema_dict: dict   # FormSchema serialized to dict


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
    ats_type: str | None = None


@dataclass
class DuplicateCheckOutput:
    is_duplicate: bool
    reason: str | None = None
    existing_run_id: str | None = None


@dataclass
class MapAnswersInput:
    run_id: str
    form_schema_dict: dict   # FormSchema serialized to dict
    job_description: str = ""
    account_email: str | None = None  # alias to use as the form's email
    job_location: str | None = None   # decides which country unscoped authorization questions are about


@dataclass
class MapAnswersOutput:
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
class FillInput:
    run_id: str
    job_url: str
    form_schema_dict: dict
    field_mappings: list[dict]
    job_description: str
    cover_letter_path: str | None
    cover_letter_text: str | None = None
    account_email: str | None = None
    job_location: str | None = None
    review_round: int = 1  # the review round this fill's evidence is for
    # Re-fill after a review approval: exactly the approved mappings (no new model mapping), and the
    # browser stays open for SUBMIT unless a required field without an approved answer appeared.
    refill: bool = False


@dataclass
class FillOutput:
    form_schema_dict: dict      # including fields that appeared while filling
    field_mappings: list[dict]
    written: dict[str, str]     # key -> what was written/chosen (the submit gate checks the page against it)
    # Re-fill only: fields that appeared without an approved answer (rows shaped like schema fields)
    unmapped_new_fields: list[dict] = field(default_factory=list)
    # Re-fill only: what fields without an approved answer showed after code's fill, before any agent ran
    # (agent.fill.FillContext.baseline; shape owned by agent/fill.py). SUBMIT checks the page against it.
    baseline: dict | None = None


@dataclass
class MapNewFieldsInput:
    """Fields that appeared on a re-fill without an approved answer: mapped for the next review round."""
    run_id: str
    fields: list[dict]          # rows shaped like schema fields (FillOutput.unmapped_new_fields)
    approved_mappings: list[dict]
    job_description: str
    review_round: int           # the round they are reviewed in
    account_email: str | None = None
    job_location: str | None = None


@dataclass
class SubmitInput:
    run_id: str
    job_url: str
    form_schema_dict: dict      # as filled (FillOutput.form_schema_dict)
    field_mappings: list[dict]  # the approved mappings
    written: dict[str, str]
    job_description: str
    cover_letter_path: str | None
    cover_letter_text: str | None
    account_email: str
    mapped_form_dict: dict | None = None  # the MAP_FORM schema: a re-opened form is re-filled from it
    baseline: dict | None = None  # FillOutput.baseline of the approved re-fill


@dataclass
class SubmitOutput:
    confirmation_url: str | None
    confirmation_text: str | None
