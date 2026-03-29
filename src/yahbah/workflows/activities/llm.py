"""
LLM activities — stateless, retryable.
"""
import json
from dataclasses import asdict

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


@activity.defn
async def generate_cover_letter_activity(input: CoverLetterInput) -> CoverLetterOutput:
    profile = await _load_profile()
    generator = CoverLetterGenerator()
    cover_letter_text = await generator.generate(input.job_description, profile)

    registry = BrowserRegistry.instance()
    cover_letter_path = registry.artifact_path(input.run_id, "cover_letter.txt")
    with open(cover_letter_path, "w") as f:
        f.write(cover_letter_text)

    await persist_artifact_activity(
        input.run_id,
        "cover_letter",
        cover_letter_path,
    )

    return CoverLetterOutput(
        cover_letter_path=cover_letter_path,
        cover_letter_text=cover_letter_text,
    )
