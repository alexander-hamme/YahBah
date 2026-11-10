"""
LLM activities — stateless, retryable.

  map_answers_activity            answer every form field (llm/answers.py) and score the match
  map_new_fields_activity         answer fields that appeared on a review re-fill, for the next review round
  generate_cover_letter_activity  cover letter text + PDF
  extract_job_metadata_activity   title, company, salary, technologies, the job's offers (C9), ...
"""
import json
from dataclasses import asdict
from pathlib import Path
from fpdf import FPDF

from pydantic import BaseModel, ConfigDict
from temporalio import activity
from temporalio.exceptions import ApplicationError

from yahbah.agent.session import artifact_path
from yahbah.config import load_prompts_config
from yahbah.llm.answers import AnswerEngine, AnswerPlan, JobContext, NeedsAnswer
from yahbah.llm.client import OllamaClient
from yahbah.llm.cover_letter import CoverLetterGenerator
from yahbah.llm.job_offers import JobOffersReply, stated, validate_offers
from yahbah.llm.facts import FactCatalog, FactsConfigError, load_facts
from yahbah.llm.logs import use_run_log_dir
from yahbah.llm.match_score import compute_match_score
from yahbah.llm.profile import profile_text
from yahbah.run_state import attempt_tag
from yahbah.schemas import (
    CoverLetterInput,
    CoverLetterOutput,
    FillInput,
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


def field_from_row(row: dict) -> FormField:
    """A FormField from a schema-field row (agent/fill.py's rows: `type`, not `field_type`)."""
    return FormField(key=row["key"], label=row["label"], field_type=row["type"], required=bool(row.get("required")),
                     options=row.get("options") or [], options_total=row.get("options_total"),
                     empty_value=row.get("empty_value"), autocomplete=row.get("autocomplete"),
                     description=row.get("description") or "", section=row.get("section") or "")


def load_catalog(profile, account_email: str | None) -> FactCatalog:
    """The run's fact catalog; an invalid or unmigrated personal.yaml fails the run without retries."""
    try:
        return load_facts(profile, account_email=account_email)
    except FactsConfigError as exc:
        raise ApplicationError(str(exc), type="internal_error", non_retryable=True) from exc


def job_context(input_: MapAnswersInput | MapNewFieldsInput | FillInput) -> JobContext:
    """What the answer engine is told about the job (untrusted page data, context only)."""
    return JobContext(location=input_.job_location, description=input_.job_description,
                      company=input_.job_company, title=input_.job_title, offers=input_.job_offers)


async def persist_answer_plan(run_id: str, plan: AnswerPlan, stage: str, review_round: int | None) -> None:
    """The plan's diagnostics (contract §8 `answer_plan`: fact ids, never fact values)."""
    path = artifact_path(run_id, f"answer_plan_{stage}_{attempt_tag()}.json")
    with open(path, "w") as f:
        json.dump(plan.to_artifact(stage, review_round), f, indent=2, ensure_ascii=False)
    await persist_artifact_activity(run_id, "answer_plan", path, {"stage": stage, "review_round": review_round})


def needs_answer_error(exc: NeedsAnswer) -> ApplicationError:
    return ApplicationError(str(exc), type="needs_answer", non_retryable=True)


@activity.defn
@heartbeating
async def map_answers_activity(input_: MapAnswersInput) -> MapAnswersOutput:
    use_run_log_dir(input_.run_id)
    profile = await _load_profile()
    catalog = load_catalog(profile, input_.account_email)  # profile.email is the run's alias
    fields = [FormField(**f) for f in input_.form_schema_dict.get("fields", [])]

    plan = await AnswerEngine().plan(fields, job_context(input_), catalog)
    await persist_answer_plan(input_.run_id, plan, "map_answers", None)
    try:
        plan.raise_if_blocked()
    except NeedsAnswer as exc:
        raise needs_answer_error(exc) from exc
    activity.logger.info(
        "[map_answers] " + ", ".join(f"[{m.key}] {m.label[:30]!r}<-{m.source}" for m in plan.mappings))

    # Compute match score (separate LLM call)
    match_score, match_rationale = None, None
    try:
        match_score, match_rationale = await compute_match_score(
            profile_text(profile, contact=False), input_.job_description
        )
        activity.logger.info(
            f"[match] Score: {match_score}% — {match_rationale}"
        )

        await fenced_update(input_.run_id, match_score=match_score, match_rationale=match_rationale)
    except Exception as exc:
        activity.logger.warning(f"[match] Failed to compute match score: {exc}")

    return MapAnswersOutput(
        field_mappings=[asdict(m) for m in plan.mappings],
        match_score=match_score,
        match_rationale=match_rationale,
    )


@activity.defn
@heartbeating
async def map_new_fields_activity(input_: MapNewFieldsInput) -> MapAnswersOutput:
    """Answer required fields that appeared when an approved application was re-filled (contract §4).
    Their rows (fill-evidence shape, nothing written yet) are saved as the `new_fields` artifact of
    the review round they go to; the approved mappings are the engine's prior answers."""
    use_run_log_dir(input_.run_id)
    profile = await _load_profile()
    catalog = load_catalog(profile, input_.account_email)
    fields = [field_from_row(f) for f in input_.fields]
    plan = await AnswerEngine().plan(fields, job_context(input_), catalog,
                                     prior=input_.approved_mappings)
    await persist_answer_plan(input_.run_id, plan, "map_new_fields", input_.review_round)
    try:
        plan.raise_if_blocked()
    except NeedsAnswer as exc:
        raise needs_answer_error(exc) from exc

    by_key = {m.key: m for m in plan.mappings}
    rows = [{"key": f.key, "label": f.label, "type": f.field_type, "required": f.required, "options": f.options,
             "answer": (m := by_key.get(f.key)) and m.value, "source": m and m.source,
             "basis": list(m.basis) if m else [], "generated": bool(m and m.generated),
             "written": None, "page_value": None, "ok": None}
            for f in fields]
    path = artifact_path(input_.run_id, f"new_fields_{input_.review_round}_{attempt_tag()}.json")
    with open(path, "w") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    await persist_artifact_activity(input_.run_id, "new_fields", path, {"review_round": input_.review_round})
    activity.logger.info(
        "[map_new_fields] " + ", ".join(f"[{m.key}] {m.label[:30]!r}<-{m.source}" for m in plan.mappings))
    return MapAnswersOutput(field_mappings=[asdict(m) for m in plan.mappings])


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


def _require_offers(schema: dict) -> None:
    schema.setdefault("required", []).append("offers")


class _JobMetadata(BaseModel):
    # offers is always decoded, so the model says "unknown" rather than leaving claims out; its default
    # only serves validation.
    model_config = ConfigDict(json_schema_extra=_require_offers)
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
    offers: JobOffersReply | None = None  # contract C9 before the code checks (llm/job_offers.py)


class _JobTechExtraction(BaseModel):
    technologies: list[str] | None = None
    specialties: list[str] | None = None


async def _extract_metadata(llm: OllamaClient, job_description: str) -> _JobMetadata:
    """Extract title, company, location, salary, summary and the job's offers (C9) from the full posting."""
    prompts = load_prompts_config()["prompts"]
    return await llm.generate_structured(
        system_prompt=prompts["job_metadata"],
        user_prompt=f"Job posting text:\n\n{job_description}",
        response_model=_JobMetadata,
    )


def _too_short(job_description: str) -> bool:
    return not job_description or len(job_description.strip()) < 50


def _offers(metadata: _JobMetadata, job_description: str) -> dict | None:
    return validate_offers(metadata.offers.model_dump(), job_description) if metadata.offers else None


async def extract_job_offers(llm: OllamaClient, description: str) -> dict | None:
    """The job's validated offers (C9): the job_metadata call, then the code checks against `description`.
    For callers without the metadata activity (scripts/run_pipeline.py, the extraction eval)."""
    if _too_short(description):
        return None
    return _offers(await _extract_metadata(llm, description), description)


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
    if _too_short(input.job_description):
        activity.logger.warning("[metadata] Job description too short — skipping LLM extraction")
        return JobMetadataOutput()

    llm = OllamaClient("driver")

    # Call 1: metadata
    metadata = await _extract_metadata(llm, input.job_description)
    job_offers = _offers(metadata, input.job_description)
    activity.logger.info(
        f"[metadata] Extracted: title={metadata.title!r}, company={metadata.company!r}, "
        f"location={metadata.location!r}, summary={metadata.description_summary!r}, "
        f"salary={metadata.salary_min}–{metadata.salary_max}, job offers: {list(stated(job_offers))}"
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
        job_offers=job_offers,
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