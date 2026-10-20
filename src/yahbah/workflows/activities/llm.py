"""
LLM activities — stateless, retryable.

  map_answers_activity            map every form field to an answer (and score the match)
  map_new_fields_activity         map fields that appeared on a review re-fill, for the next review round
  generate_cover_letter_activity  cover letter text + PDF
  extract_job_metadata_activity   title, company, salary, technologies, ...
"""
import json
from dataclasses import asdict
from pathlib import Path
from fpdf import FPDF

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from yahbah.agent.session import artifact_path
from yahbah.config import load_prompts_config
from yahbah.llm.client import OllamaClient
from yahbah.llm.field_mapper import FieldMapper, NeedsAnswer
from yahbah.llm.cover_letter import CoverLetterGenerator
from yahbah.llm.logs import use_run_log_dir
from yahbah.llm.profile import profile_text
from yahbah.run_state import attempt_tag
from yahbah.schemas import (
    CoverLetterInput,
    CoverLetterOutput,
    FieldMapping,
    FormField,
    JobMetadataInput,
    JobMetadataOutput,
    MapAnswersInput,
    MapAnswersOutput,
    MapNewFieldsInput,
)
from yahbah.workflows.activities.db_ops import fenced_update, persist_artifact_activity
from yahbah.workflows.activities.heartbeat import heartbeating
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
@heartbeating
async def map_answers_activity(input_: MapAnswersInput) -> MapAnswersOutput:
    use_run_log_dir(input_.run_id)
    profile = await _load_profile()
    fields = [FormField(**f) for f in input_.form_schema_dict.get("fields", [])]

    mapper = FieldMapper()
    try:
        mappings = await mapper.map(fields, profile, job_description=input_.job_description,
                                    job_location=input_.job_location)
    except NeedsAnswer as exc:
        raise ApplicationError(str(exc), type="needs_answer", non_retryable=True) from exc

    # Override the email field with the trackable alias so company replies
    # can be matched back to this specific application run.
    if input_.account_email:
        for m in mappings:
            if m.source == "email":
                m.value = input_.account_email

    activity.logger.info(
        "[map_answers] " + ", ".join(f"[{m.key}] {m.label[:30]!r}<-{m.source}" for m in mappings))
    mappings_path = artifact_path(input_.run_id, "field_mappings.json")
    with open(mappings_path, "w") as f:
        json.dump([asdict(m) for m in mappings], f, indent=2, ensure_ascii=False)
    await persist_artifact_activity(
        input_.run_id,
        "field_mappings",
        mappings_path,
    )

    # Compute match score (separate LLM call)
    match_score, match_rationale = None, None
    try:
        match_score, match_rationale = await mapper.compute_match_score(
            profile_text(profile, contact=False), input_.job_description
        )
        activity.logger.info(
            f"[match] Score: {match_score}% — {match_rationale}"
        )

        await fenced_update(input_.run_id, match_score=match_score, match_rationale=match_rationale)
    except Exception as exc:
        activity.logger.warning(f"[match] Failed to compute match score: {exc}")

    return MapAnswersOutput(
        field_mappings=[asdict(m) for m in mappings],
        match_score=match_score,
        match_rationale=match_rationale,
    )


@activity.defn
@heartbeating
async def map_new_fields_activity(input_: MapNewFieldsInput) -> MapAnswersOutput:
    """Map required fields that appeared when an approved application was re-filled (contract §4).
    Their rows (fill-evidence shape, nothing written yet) are saved as the `new_fields` artifact of
    the review round they go to; the approved mappings are the mapper's prior context."""
    use_run_log_dir(input_.run_id)
    profile = await _load_profile()
    fields = [FormField(key=f["key"], label=f["label"], field_type=f["type"], required=bool(f.get("required")),
                        options=f.get("options") or [], options_total=f.get("options_total"),
                        empty_value=f.get("empty_value"), autocomplete=f.get("autocomplete"))
              for f in input_.fields]
    prior = [FieldMapping(**m) for m in input_.approved_mappings]
    try:
        mappings = await FieldMapper().map(fields, profile, input_.job_description, prior=prior,
                                           job_location=input_.job_location)
    except NeedsAnswer as exc:
        raise ApplicationError(str(exc), type="needs_answer", non_retryable=True) from exc
    for m in mappings:
        if m.source == "email" and input_.account_email:
            m.value = input_.account_email

    by_key = {m.key: m for m in mappings}
    rows = [{"key": f.key, "label": f.label, "type": f.field_type, "required": f.required, "options": f.options,
             "answer": (m := by_key.get(f.key)) and m.value, "source": m and m.source,
             "basis": list(m.basis) if m else [], "generated": bool(m and m.generated),
             "written": None, "page_value": None, "ok": None}
            for f in fields]
    path = artifact_path(input_.run_id, f"new_fields_{input_.review_round}_{attempt_tag()}.json")
    with open(path, "w") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    await persist_artifact_activity(input_.run_id, "new_fields", path, {"review_round": input_.review_round})
    activity.logger.info("[map_new_fields] " + ", ".join(f"[{m.key}] {m.label[:30]!r}<-{m.source}" for m in mappings))
    return MapAnswersOutput(field_mappings=[asdict(m) for m in mappings])


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
    use_run_log_dir(input.run_id)
    profile = await _load_profile()
    generator = CoverLetterGenerator()
    cover_letter_text = await generator.generate(input.job_description, profile)

    cl_filename = load_prompts_config().get("profile", {}).get("cover_letter_filename", "cover_letter.pdf")
    cover_letter_path = artifact_path(input.run_id, cl_filename)
    _text_to_pdf(cover_letter_text, cover_letter_path)

    await persist_artifact_activity(
        input.run_id,
        "cover_letter",
        cover_letter_path,
        {"text": cover_letter_text},  # shown in review (contract §8)
    )

    return CoverLetterOutput(
        cover_letter_path=cover_letter_path,
        cover_letter_text=cover_letter_text,
    )


class _JobMetadata(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description_summary: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    company_website: str | None = None
    industry: str | None = None
    company_description: str | None = None
    work_model: str | None = None
    seniority_level: str | None = None
    experience_required: str | None = None
    posted_date: str | None = None


class _JobTechExtraction(BaseModel):
    technologies: list[str] | None = None
    specialties: list[str] | None = None


async def _extract_metadata(llm: OllamaClient, job_description: str) -> _JobMetadata:
    """Extract title, company, location, salary, summary from the full posting."""
    prompts = load_prompts_config()["prompts"]
    return await llm.generate_structured(
        system_prompt=prompts["job_metadata"],
        user_prompt=f"Job posting text:\n\n{job_description}",
        response_model=_JobMetadata,
    )


async def _extract_tech(llm: OllamaClient, job_description: str) -> _JobTechExtraction:
    """Extract technologies and specialties from Requirements/Bonus sections."""
    prompts = load_prompts_config()["prompts"]
    return await llm.generate_structured(
        system_prompt=prompts["job_tech_extraction"],
        user_prompt=f"Job posting text:\n\n{job_description}",
        response_model=_JobTechExtraction,
    )


@activity.defn
async def extract_job_metadata_activity(input: JobMetadataInput) -> JobMetadataOutput:
    """
    Two sequential LLM calls:
      1. Metadata: title, company, location, salary, summary (scans full posting)
      2. Tech extraction: technologies + specialties (focuses on Requirements/Bonus)

    To run in parallel later, convert to asyncio.gather() or split into
    separate Temporal activities.
    """
    use_run_log_dir(input.run_id)
    if not input.job_description or len(input.job_description.strip()) < 50:
        activity.logger.warning("[metadata] Job description too short — skipping LLM extraction")
        return JobMetadataOutput()

    llm = OllamaClient("driver")

    # Call 1: metadata
    metadata = await _extract_metadata(llm, input.job_description)
    activity.logger.info(
        f"[metadata] Extracted: title={metadata.title!r}, company={metadata.company!r}, "
        f"location={metadata.location!r}, summary={metadata.description_summary!r}, "
        f"salary={metadata.salary_min}–{metadata.salary_max}"
    )

    # Call 2: tech extraction
    tech = await _extract_tech(llm, input.job_description)
    activity.logger.info(
        f"[metadata] Technologies: {tech.technologies}, Specialties: {tech.specialties}"
    )

    return JobMetadataOutput(
        title=metadata.title,
        company=metadata.company,
        location=metadata.location,
        description_summary=metadata.description_summary,
        salary_min=metadata.salary_min,
        salary_max=metadata.salary_max,
        company_website=metadata.company_website,
        industry=metadata.industry,
        company_description=metadata.company_description,
        work_model=metadata.work_model,
        seniority_level=metadata.seniority_level,
        experience_required=metadata.experience_required,
        posted_date=metadata.posted_date,
        technologies=tech.technologies,
        specialties=tech.specialties,
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