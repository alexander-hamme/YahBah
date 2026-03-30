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

# Reddit job — has EEO dropdowns, GDPR demographic consent checkbox, location
# autocomplete, and privacy consent dropdown.
TEST_URL = "https://boards.greenhouse.io/reddit/jobs/7074776"
TEST_RUN_ID = "test-fill-run"

RESUME_PATH = str(REPO_ROOT / "files/Alexander-Hamme-Resume.pdf")
COVER_LETTER_PATH = str(REPO_ROOT / "artifacts/test-fill-run/cover_letter.pdf")


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


# ── Test: extract → LLM map → fill (full pipeline, no submission) ─────────────

async def test_fill_from_extract() -> None:
    """
    Extracts the live form schema, runs the LLM field mapper, fills the form.
    Does NOT submit. Takes screenshots at key stages.
    """
    print("\n=== test_fill_from_extract ===")
    reg = BrowserRegistry.instance()

    page = reg.get_page(TEST_RUN_ID)
    if page is None:
        page = await reg.open_page(TEST_RUN_ID, TEST_URL)

    await reg.screenshot(TEST_RUN_ID, "01_page_loaded")

    extractor = GreenhouseExtractor(page)
    schema, job_description = await extractor.extract()

    print(f"\nExtracted {len(schema.fields)} fields:")
    for f in schema.fields:
        print(f"  {f.label!r:50s} type={f.field_type:10s} required={f.required}")

    await reg.screenshot(TEST_RUN_ID, "02_after_extract")

    profile = await _load_profile()
    mapper = FieldMapper()
    result = await mapper.map(schema, profile, job_description=job_description)

    print(f"\nField mappings ({len(result.field_mappings)}):")
    for m in result.field_mappings:
        print(f"  {m.form_label!r:50s} → {m.mapped_to!r:25s} = {m.value[:50]!r}  (conf={m.confidence:.2f})")

    cover_letter_path, cover_letter_text = await _generate_cover_letter(job_description)

    filler = GreenhouseFiller(page, form_schema=schema)
    await filler.fill(result.field_mappings, cover_letter_path, cover_letter_text)

    await reg.screenshot(TEST_RUN_ID, "03_after_fill")

    # Scroll to bottom to capture checkbox and EEO section
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await reg.screenshot(TEST_RUN_ID, "04_bottom_of_form")

    print(f"\nScreenshots saved to: {REPO_ROOT / 'artifacts' / TEST_RUN_ID}")
    print("test_fill_from_extract PASSED — check screenshots to verify fields")


# ── Runner ────────────────────────────────────────────────────────────────────

async def main() -> None:
    reg = BrowserRegistry.instance()
    await reg.start()

    try:
        await test_fill_from_extract()
    finally:
        await reg.close_page(TEST_RUN_ID)
        await reg.stop()


if __name__ == "__main__":
    asyncio.run(main())
