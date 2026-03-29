"""
Test script for GreenhouseFiller — exercises deterministic form filling
against a live Greenhouse page using hardcoded field mappings.

Usage:
    uv run python test/yahbah/browser/test_form_fill.py

Two test functions:
  test_fill_hardcoded  — fills using hand-written FieldMappings (no LLM)
  test_fill_from_extract — extracts schema from the page, runs LLM mapper,
                           then fills (full pipeline, no submission)
"""
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

# Override artifacts dir to an absolute path so screenshots always land in the
# right place regardless of the CWD the script is launched from.
import os
os.environ.setdefault("ARTIFACTS_DIR", str(REPO_ROOT / "artifacts"))

from sqlalchemy import select

from yahbah.browser.greenhouse import GreenhouseExtractor, GreenhouseFiller
from yahbah.browser.manager import BrowserRegistry
from yahbah.db.models import ApplicantProfile
from yahbah.db.session import AsyncSessionLocal
from yahbah.llm.cover_letter import CoverLetterGenerator
from yahbah.llm.field_mapper import FieldMapper
from yahbah.workflows.activities.llm import _text_to_pdf
from yahbah.schemas import FieldMapping, FormField, FormSchema

# ── Config ────────────────────────────────────────────────────────────────────

TEST_URL = "https://job-boards.greenhouse.io/paradigminccareersopenpositions/jobs/4647304005"
TEST_RUN_ID = "test-fill-run"

RESUME_PATH = str(REPO_ROOT / "files/Alexander-Hamme-Resume.pdf")
COVER_LETTER_PATH = str(REPO_ROOT / "artifacts/test-fill-run/cover_letter.pdf")

# Hardcoded mappings — mirrors what the LLM would produce for a typical
# Greenhouse form. Edit values here to test specific fields.
HARDCODED_MAPPINGS = [
    FieldMapping(form_label="First Name",       mapped_to="first_name",  value="Alexander",                                confidence=1.0),
    FieldMapping(form_label="Last Name",        mapped_to="last_name",   value="Hamme",                                    confidence=1.0),
    FieldMapping(form_label="Email",            mapped_to="email",       value="alexhamme96@gmail.com",                    confidence=1.0),
    FieldMapping(form_label="Phone",            mapped_to="phone",       value="+1-857-264-7620",                          confidence=1.0),
    FieldMapping(form_label="LinkedIn Profile", mapped_to="linkedin_url",value="https://linkedin.com/in/alexander-hamme",  confidence=1.0),
    FieldMapping(form_label="Resume",           mapped_to="resume",      value=RESUME_PATH,                                confidence=1.0),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _load_profile() -> ApplicantProfile:
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(ApplicantProfile).limit(1))).scalar_one()


async def _generate_cover_letter(job_description: str = "") -> tuple[str, str]:
    """
    Generates a cover letter via the LLM, saves it as a PDF, and returns
    (cover_letter_path, cover_letter_text).
    """
    path = Path(COVER_LETTER_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    profile = await _load_profile()
    generator = CoverLetterGenerator()
    text = await generator.generate(job_description, profile)
    _text_to_pdf(text, str(path))
    print(f"Cover letter generated and saved: {path}")
    return str(path), text


# ── Test 1: fill with hardcoded mappings (no LLM) ────────────────────────────

async def test_fill_hardcoded() -> None:
    """
    Opens the page, fills fields using HARDCODED_MAPPINGS.
    Does NOT submit. Takes a screenshot after filling.
    """
    print("\n=== test_fill_hardcoded ===")
    reg = BrowserRegistry.instance()
    page = await reg.open_page(TEST_RUN_ID, TEST_URL)
    cover_letter_path, cover_letter_text = await _generate_cover_letter()

    filler = GreenhouseFiller(page)
    await filler.fill(HARDCODED_MAPPINGS, cover_letter_path, cover_letter_text)

    screenshot = await reg.screenshot(TEST_RUN_ID, "hardcoded_fill")
    print(f"Screenshot saved: {screenshot}")
    print("test_fill_hardcoded PASSED — check screenshot to verify fields")


# ── Test 2: extract → LLM map → fill (full pipeline, no submission) ──────────

async def test_fill_from_extract() -> None:
    """
    Extracts the live form schema, runs the LLM field mapper, fills the form.
    Does NOT submit. Prints each mapping before filling.
    """
    print("\n=== test_fill_from_extract ===")
    reg = BrowserRegistry.instance()

    # Reuse existing page if open, otherwise open fresh
    page = reg.get_page(TEST_RUN_ID)
    if page is None:
        page = await reg.open_page(TEST_RUN_ID, TEST_URL)

    extractor = GreenhouseExtractor(page)
    schema, job_description = await extractor.extract()

    print(f"Extracted {len(schema.fields)} fields:")
    for f in schema.fields:
        print(f"  {f.label!r:40s} type={f.field_type} required={f.required}")

    profile = await _load_profile()
    mapper = FieldMapper()
    result = await mapper.map(schema, profile, job_description=job_description)

    print(f"\nField mappings ({len(result.field_mappings)}):")
    for m in result.field_mappings:
        print(f"  {m.form_label!r:40s} → {m.mapped_to} = {m.value[:60]!r}  (conf={m.confidence})")

    cover_letter_path, cover_letter_text = await _generate_cover_letter(job_description)
    filler = GreenhouseFiller(page, form_schema=schema)
    await filler.fill(result.field_mappings, cover_letter_path, cover_letter_text)

    screenshot = await reg.screenshot(TEST_RUN_ID, "llm_fill")
    print(f"\nScreenshot saved: {screenshot}")
    print("test_fill_from_extract PASSED — check screenshot to verify fields")


# ── Runner ────────────────────────────────────────────────────────────────────

async def main() -> None:
    reg = BrowserRegistry.instance()
    await reg.start()

    try:
        await test_fill_hardcoded()
        await test_fill_from_extract()
    finally:
        await reg.close_page(TEST_RUN_ID)
        await reg.stop()


if __name__ == "__main__":
    asyncio.run(main())
