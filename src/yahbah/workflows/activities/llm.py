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


# if __name__ == "__main__":
#
#     _text_to_pdf("""Dear Paradigm Recruiting Team,
# I am a senior machinelearning engineer with seven years of experience turning
# cuttingedge research into production grade AI systems for highstakes government
# R&D programs. My track record of delivering performancecritical, scalable
# solutions—most recently a LLMdriven archival intelligence platform that transformed
# decades of unstructured code into a searchable knowledge graph—directly aligns with
# Paradigm’s mission to """, "test.pdf")