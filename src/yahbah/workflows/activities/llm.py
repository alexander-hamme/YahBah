"""
LLM activities — stateless, retryable.
"""
import json
from dataclasses import asdict
from pathlib import Path
from fpdf import FPDF

from temporalio import activity

from yahbah.llm.field_mapper import FieldMapper
from yahbah.llm.cover_letter import CoverLetterGenerator
from yahbah.schemas import (
    CoverLetterInput,
    CoverLetterOutput,
    FormField,
    FormSchema,
    JobMetadataInput,
    JobMetadataOutput,
    MapFieldsInput,
    MapFieldsOutput,
)
from yahbah.workflows.activities.db_ops import persist_artifact_activity
from yahbah.browser.manager import BrowserRegistry
from yahbah.db.session import AsyncSessionLocal
from yahbah.db.models import ApplicantProfile
from sqlalchemy import select


# _FONT_PATH = Path(__file__).resolve().parents[4] / "files" / "SNPro-VariableFont_wght.ttf"
# _FONT_NAME = "SNPro"
# _FONT_PATH = Path(__file__).resolve().parents[4] / "files" / "NunitoSans-VariableFont_YTLC,opsz,wdth,wght.ttf"
# _FONT_NAME = "NunitoSans"
# _FONT_PATH = None
# _FONT_NAME = "Helvetica"
_FONT_PATH = Path(__file__).resolve().parents[4] / "files" / "CrimsonText-Regular.ttf"
_FONT_NAME = "CrimsonText"

async def _load_profile() -> ApplicantProfile:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ApplicantProfile).limit(1))
        profile = result.scalar_one_or_none()
        if profile is None:
            raise RuntimeError("No ApplicantProfile found. Run scripts/seed_profile.py first.")
        return profile


@activity.defn
async def map_fields_activity(input: MapFieldsInput) -> MapFieldsOutput:
    profile = await _load_profile()
    raw = input.form_schema_dict
    form_schema = FormSchema(
        fields=[FormField(**f) for f in raw.get("fields", [])],
        page_url=raw.get("page_url", ""),
        page_title=raw.get("page_title", ""),
    )

    mapper = FieldMapper()
    result = await mapper.map(form_schema, profile, job_description=input.job_description)

    # Persist mappings as artifact
    registry = BrowserRegistry.instance()
    mappings_path = registry.artifact_path(input.run_id, "field_mappings.json")
    with open(mappings_path, "w") as f:
        json.dump([asdict(m) for m in result.field_mappings], f, indent=2)
    await persist_artifact_activity(
        input.run_id,
        "field_mappings",
        mappings_path,
    )

    return MapFieldsOutput(
        field_mappings=[asdict(m) for m in result.field_mappings],
    )


def _text_to_pdf(text: str, path: str) -> None:
    """Renders plain text as a letter-size PDF with 1-inch margins in Nunito Sans."""
    pdf = FPDF(format="Letter")
    if _FONT_PATH is not None:
        pdf.add_font(_FONT_NAME, fname=str(_FONT_PATH))
    pdf.set_margins(25.4, 25.4, 25.4)
    pdf.set_auto_page_break(auto=True, margin=25.4)
    pdf.add_page()
    pdf.set_font(_FONT_NAME, size=12)
    pdf.multi_cell(0, 6, text)
    pdf.output(path)


@activity.defn
async def generate_cover_letter_activity(input: CoverLetterInput) -> CoverLetterOutput:
    profile = await _load_profile()
    generator = CoverLetterGenerator()
    cover_letter_text = await generator.generate(input.job_description, profile)

    registry = BrowserRegistry.instance()
    cover_letter_path = registry.artifact_path(input.run_id, "cover_letter.pdf")
    _text_to_pdf(cover_letter_text, cover_letter_path)

    await persist_artifact_activity(
        input.run_id,
        "cover_letter",
        cover_letter_path,
    )

    return CoverLetterOutput(
        cover_letter_path=cover_letter_path,
        cover_letter_text=cover_letter_text,
    )


from pydantic import BaseModel

from yahbah.llm.client import OllamaClient


class _JobMetadata(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description_summary: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    technologies: list[str] | None = None


@activity.defn
async def extract_job_metadata_activity(input: JobMetadataInput) -> JobMetadataOutput:
    """
    Uses the LLM to extract structured metadata from the raw job description:
      - title, company, location (fallbacks — DOM scraping is preferred)
      - description_summary: ≤40 word summary of the role
      - salary_min / salary_max: annual salary bounds in USD (integers), or null
    """
    if not input.job_description or len(input.job_description.strip()) < 50:
        activity.logger.warning("[metadata] Job description too short — skipping LLM extraction")
        return JobMetadataOutput()

    llm = OllamaClient()
    result = await llm.generate_structured(
        system_prompt=(
            "You are extracting structured metadata from a job posting. "
            "Return valid JSON with these fields:\n"
            '- "title": the job title (e.g. "Senior Software Engineer"). Use null if unclear.\n'
            '- "company": the company name. Use null if unclear.\n'
            '- "location": the job location (e.g. "San Francisco, CA" or "Remote"). Use null if unclear.\n'
            '- "description_summary": a concise summary of the role in at most 40 words. '
            "Focus on what the role does, not the company.\n"
            '- "salary_min": the minimum annual salary in USD as an integer (no decimals, '
            "no currency symbols). Use null if not stated.\n"
            '- "salary_max": the maximum annual salary in USD as an integer. Use null if not stated.\n'
            '- "technologies": a JSON array of specific technologies, tools, frameworks, '
            "and platforms mentioned (e.g. [\"Spark\", \"PyTorch\", \"AWS\"]). Normalize names "
            "(e.g. \"k8s\" → \"Kubernetes\", \"GCP\" → \"Google Cloud\"). Return empty array if none found.\n"
            "If salary is given as hourly, multiply by 2080 to annualize. "
            "If a single number is given, use it for both min and max."
        ),
        user_prompt=f"Job posting text:\n\n{input.job_description[:3000]}",
        response_model=_JobMetadata,
    )

    activity.logger.info(
        f"[metadata] Extracted: title={result.title!r}, company={result.company!r}, "
        f"location={result.location!r}, summary={result.description_summary!r}, "
        f"salary={result.salary_min}–{result.salary_max}, "
        f"technologies={result.technologies}"
    )
    return JobMetadataOutput(
        title=result.title,
        company=result.company,
        location=result.location,
        description_summary=result.description_summary,
        salary_min=result.salary_min,
        salary_max=result.salary_max,
        technologies=result.technologies,
    )


# if __name__ == "__main__":
#
#     _text_to_pdf("""Dear Paradigm Recruiting Team,
# I am a senior machinelearning engineer with seven years of experience turning
# cuttingedge research into production grade AI systems for highstakes government
# R&D programs. My track record of delivering performancecritical, scalable
# solutions—most recently a LLMdriven archival intelligence platform that transformed
# decades of unstructured code into a searchable knowledge graph—directly aligns with
# Paradigm’s mission to """, "test.pdf")