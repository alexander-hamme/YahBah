"""
Test script for job metadata + tech extraction.

Fetches a real job posting via Playwright, extracts the description text
the same way the pipeline does, then runs the two LLM calls and prints results.

Usage:
    uv run python test/yahbah/test_metadata_extraction.py [URL]

Default URL: https://tulip.co/careers/job-posting/?gh_jid=7650609003&gh_src=my.greenhouse.search

Expected for Tulip:
    location: Somerville, MA (or similar)
    salary_min: 145000
    salary_max: 190000
"""
import asyncio
import sys
from pathlib import Path

# Make src/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from playwright.async_api import async_playwright
from yahbah.llm.client import OllamaClient
from yahbah.config import load_prompts_config
from pydantic import BaseModel


# Same models as the activity
class JobMetadata(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description_summary: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    company_website: str | None = None
    company_description: str | None = None
    posted_date: str | None = None


class JobTechExtraction(BaseModel):
    technologies: list[str] | None = None
    specialties: list[str] | None = None


# Same selectors as greenhouse.py
DESCRIPTION_SELECTORS = [
    "#content",
    ".job-description",
    "[data-testid='job-description']",
    "article",
    "main",
]


async def _try_selectors(page_or_frame) -> str:
    """Try description selectors on a page or frame."""
    for sel in DESCRIPTION_SELECTORS:
        el = await page_or_frame.query_selector(sel)
        if el:
            text = await el.inner_text()
            if len(text) > 100:
                return text
    return await page_or_frame.evaluate("document.body.innerText") or ""


async def fetch_job_description(url: str) -> str:
    """Fetch and extract job description text from a URL using Playwright.

    Mirrors the pipeline: try parent page first, fall back to iframe if too short.
    """
    print(f"\n--- Fetching: {url}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30_000)

        # Try parent page first
        text = await _try_selectors(page)
        print(f"--- Parent page text: {len(text)} chars")

        # If too short, try greenhouse iframe
        if len(text) < 500:
            for frame in page.frames:
                if "greenhouse" in frame.url:
                    iframe_text = await _try_selectors(frame)
                    if len(iframe_text) > len(text):
                        print(f"--- Iframe text: {len(iframe_text)} chars (using this)")
                        text = iframe_text
                        break

        print(f"--- Final description: {len(text)} chars")
        await browser.close()
        return text


async def run_extraction(job_description: str) -> None:
    """Run both LLM extraction calls and print results."""
    llm = OllamaClient()
    prompts = load_prompts_config()["prompts"]

    # Check if salary text is present in the description
    salary_keywords = ["salary", "$", "compensation", "pay range", "USD"]
    found = [kw for kw in salary_keywords if kw.lower() in job_description.lower()]
    print(f"\n--- Salary keywords found in text: {found}")
    if not found:
        print("--- WARNING: No salary-related text found in extracted description!")

    # Show last 500 chars (where salary usually lives)
    print(f"\n--- Last 500 chars of description:")
    print(job_description[-500:])

    # Call 1: Metadata
    print(f"\n--- Running metadata extraction (model: {llm._model})...")
    metadata = await llm.generate_structured(
        system_prompt=prompts["job_metadata"],
        user_prompt=f"Job posting text:\n\n{job_description}",
        response_model=JobMetadata,
    )
    print(f"\n=== METADATA RESULTS ===")
    print(f"  title:              {metadata.title}")
    print(f"  company:            {metadata.company}")
    print(f"  location:           {metadata.location}")
    print(f"  summary:            {metadata.description_summary}")
    print(f"  salary_min:         {metadata.salary_min}")
    print(f"  salary_max:         {metadata.salary_max}")
    print(f"  company_website:    {metadata.company_website}")
    print(f"  company_description:{metadata.company_description}")
    print(f"  posted_date:        {metadata.posted_date}")

    # Call 2: Tech extraction
    print(f"\n--- Running tech extraction...")
    tech = await llm.generate_structured(
        system_prompt=prompts["job_tech_extraction"],
        user_prompt=f"Job posting text:\n\n{job_description}",
        response_model=JobTechExtraction,
    )
    print(f"\n=== TECH RESULTS ===")
    print(f"  technologies:       {tech.technologies}")
    print(f"  specialties:        {tech.specialties}")

    # Assertions
    print(f"\n=== CHECKS ===")
    issues = []
    if metadata.salary_min is None:
        issues.append("salary_min is None")
    if metadata.salary_max is None:
        issues.append("salary_max is None")
    if metadata.location is None:
        issues.append("location is None")
    if metadata.company is None:
        issues.append("company is None")
    if metadata.title is None:
        issues.append("title is None")

    if issues:
        print(f"  ISSUES: {', '.join(issues)}")
    else:
        print(f"  All fields extracted successfully!")


async def main():
    default_url = "https://tulip.co/careers/job-posting/?gh_jid=7650609003&gh_src=my.greenhouse.search"
    url = sys.argv[1] if len(sys.argv) > 1 else default_url

    job_description = await fetch_job_description(url)

    if len(job_description) < 50:
        print("ERROR: Job description too short, extraction likely failed")
        sys.exit(1)

    print(f"\n--- Total description length: {len(job_description)} chars")
    await run_extraction(job_description)


if __name__ == "__main__":
    asyncio.run(main())
