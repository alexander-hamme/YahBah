"""
Browser activities — Playwright-based.

Design note: browser_extract_activity and browser_fill_and_submit_activity
are intentionally separate so that LLM activities (map_fields, cover_letter)
can run between them. Page state is managed by the BrowserRegistry in
browser/manager.py, keyed by run_id.
"""
import json
from dataclasses import asdict

from temporalio import activity

from yahbah.browser.manager import BrowserRegistry
from yahbah.browser.greenhouse import GreenhouseExtractor, GreenhouseFiller
from yahbah.schemas import (
    BrowserExtractInput,
    BrowserExtractOutput,
    BrowserFillInput,
    BrowserFillOutput,
    FormSchema,
    FieldMapping,
)
from yahbah.workflows.activities.db_ops import persist_artifact_activity


@activity.defn
async def browser_extract_activity(input: BrowserExtractInput) -> BrowserExtractOutput:
    """
    Opens the job application page, extracts the form schema, captures a
    screenshot, and keeps the page alive for the fill step.
    """
    registry = BrowserRegistry.instance()
    page = await registry.open_page(input.run_id, input.job_url)

    extractor = GreenhouseExtractor(page)
    form_schema, job_description = await extractor.extract()

    # Screenshot after extraction
    screenshot_path = await registry.screenshot(input.run_id, "after_extract")
    await persist_artifact_activity(
        input.run_id,
        "screenshot",
        screenshot_path,
        {"step": "extract_form"},
    )

    # Persist form schema as artifact
    schema_path = registry.artifact_path(input.run_id, "form_schema.json")
    with open(schema_path, "w") as f:
        json.dump(asdict(form_schema), f, indent=2)
    await persist_artifact_activity(
        input.run_id,
        "form_schema",
        schema_path,
    )

    return BrowserExtractOutput(
        form_schema_dict=asdict(form_schema),
        job_description=job_description,
    )


@activity.defn
async def browser_fill_and_submit_activity(input: BrowserFillInput) -> BrowserFillOutput:
    """
    Fills all form fields deterministically, uploads files, submits,
    and captures the confirmation page.
    """
    registry = BrowserRegistry.instance()
    page = registry.get_page(input.run_id)

    if page is None:
        # Page was lost (e.g. worker restart) — re-navigate
        activity.logger.warning(
            "Page not found in registry for run %s — re-opening", input.run_id
        )
        page = await registry.open_page(input.run_id, input.job_url)

    field_mappings = [
        FieldMapping(**m) for m in input.field_mappings
    ]

    filler = GreenhouseFiller(page)

    # Screenshot before fill
    await registry.screenshot(input.run_id, "before_fill")

    await filler.fill(field_mappings, input.cover_letter_path)

    # Screenshot after fill, before submit
    await registry.screenshot(input.run_id, "before_submit")

    confirmation_url, confirmation_text = await filler.submit()

    # Screenshot of confirmation
    screenshot_path = await registry.screenshot(input.run_id, "confirmation")
    await persist_artifact_activity(
        input.run_id,
        "screenshot",
        screenshot_path,
        {"step": "confirmation"},
    )

    # Save confirmation text as artifact
    if confirmation_text:
        conf_path = registry.artifact_path(input.run_id, "confirmation.txt")
        with open(conf_path, "w") as f:
            f.write(confirmation_text)
        await persist_artifact_activity(
            input.run_id,
            "confirmation",
            conf_path,
        )

    await registry.close_page(input.run_id)

    return BrowserFillOutput(
        confirmation_url=confirmation_url,
        confirmation_text=confirmation_text,
    )
