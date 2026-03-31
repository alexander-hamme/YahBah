"""
Browser activities — Playwright-based.

Activity sequence:
  browser_open_and_auth_activity  → opens page, handles auth wall if present
  browser_extract_activity        → extracts form schema (page already open)
  browser_fill_and_submit_activity → fills, uploads, submits, confirms

Page state is managed by BrowserRegistry (worker-level singleton), keyed by run_id.
"""
import json
from dataclasses import asdict

from temporalio import activity
from temporalio.exceptions import ApplicationError

from yahbah.browser.manager import BrowserRegistry
from yahbah.browser.greenhouse import (
    GreenhouseAuthHandler,
    GreenhouseExtractor,
    GreenhouseFiller,
)
from yahbah.credentials import (
    company_slug_from_url,
    generate_email_alias,
    generate_password,
)
from yahbah.db.models import ApplicantProfile
from yahbah.db.session import AsyncSessionLocal
from yahbah.schemas import (
    AuthInput,
    AuthOutput,
    BrowserExtractInput,
    BrowserExtractOutput,
    BrowserFillInput,
    BrowserFillOutput,
    FieldMapping,
    FormField,
    FormSchema,
)
from yahbah.workflows.activities.db_ops import (
    persist_artifact_activity,
    store_credentials_activity,
)
from yahbah.url_utils import ats_normalize, strip_tracking
from sqlalchemy import select


async def _base_email() -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ApplicantProfile).limit(1))
        profile = result.scalar_one_or_none()
        if profile is None:
            raise RuntimeError("No ApplicantProfile found")
        return profile.email


@activity.defn
async def browser_open_and_auth_activity(input: AuthInput) -> AuthOutput:
    """
    Opens the job page and handles auth walls if present.

    If an auth wall is detected:
      - generates a unique email alias + strong password
      - creates the account via GreenhouseAuthHandler
      - persists credentials to DB via store_credentials_activity
    """
    registry = BrowserRegistry.instance()
    activity.logger.info(f"[open] Opening page for run {input.run_id}: {input.job_url}")
    page = await registry.open_page(input.run_id, input.job_url)
    activity.logger.info(f"[open] Page loaded — final URL: {page.url}")

    handler = GreenhouseAuthHandler(page)
    activity.logger.info(f"[open] Checking for auth wall")
    if not await handler.needs_auth():
        activity.logger.info(f"[open] No auth wall detected for run {input.run_id}")
        return AuthOutput(auth_required=False, account_email=None, account_password=None)

    activity.logger.info(f"Auth wall detected for run {input.run_id} — creating account")

    base_email = await _base_email()
    company_slug = company_slug_from_url(input.job_url)
    email = generate_email_alias(base_email, company_slug)
    password = generate_password()

    await handler.create_account(email, password)

    # Screenshot after auth
    await registry.screenshot(input.run_id, "after_auth")

    # Persist to DB
    await store_credentials_activity(input.run_id, email, password)

    return AuthOutput(auth_required=True, account_email=email, account_password=password)


@activity.defn
async def browser_extract_activity(input: BrowserExtractInput) -> BrowserExtractOutput:
    """
    Extracts the form schema from the already-open page.
    Assumes browser_open_and_auth_activity has run first.
    """
    registry = BrowserRegistry.instance()
    page = registry.get_page(input.run_id)

    if page is None:
        # Fallback: page lost between activities (e.g. worker bounce)
        activity.logger.warning(
            f"Page not in registry for run {input.run_id} — re-opening",
        )
        page = await registry.open_page(input.run_id, input.job_url)

    activity.logger.info(f"[extract] Starting extraction — page URL: {page.url}")
    extractor = GreenhouseExtractor(page)
    try:
        form_schema, job_description = await extractor.extract()
    except ValueError as exc:
        # Job no longer available or page is not an application form —
        # no point retrying, fail immediately.
        raise ApplicationError(str(exc), non_retryable=True) from exc
    activity.logger.info(
        f"[extract] Extracted {len(form_schema.fields)} fields: "
        + ", ".join(f"{f.label!r}({f.field_type})" for f in form_schema.fields)
    )
    job_title, job_company, job_location, raw_canonical = await extractor.extract_job_metadata()
    canonical_url = ats_normalize(strip_tracking(raw_canonical))
    activity.logger.info(
        f"[extract] Job metadata — title={job_title!r} company={job_company!r} "
        f"location={job_location!r} canonical={canonical_url}"
    )

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
        canonical_url=canonical_url,
        job_title=job_title,
        job_company=job_company,
        job_location=job_location,
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
            f"Page not found in registry for run {input.run_id} — re-opening"
        )
        page = await registry.open_page(input.run_id, input.job_url)
    elif activity.info().attempt > 1:
        # Retry attempt — reload to get a clean form state rather than
        # trying to fill on top of a partially-completed or broken page.
        activity.logger.info(
            f"Retry attempt {activity.info().attempt} for run {input.run_id} — reloading page"
        )
        await page.reload(wait_until="networkidle", timeout=30_000)

    field_mappings = [FieldMapping(**m) for m in input.field_mappings]
    form_schema: FormSchema | None = None
    if input.form_schema_dict:
        raw = input.form_schema_dict
        form_schema = FormSchema(
            fields=[FormField(**f) for f in raw.get("fields", [])],
            page_url=raw.get("page_url", ""),
            page_title=raw.get("page_title", ""),
        )

    activity.logger.info(
        f"[fill] Starting fill — attempt {activity.info().attempt}, "
        f"{len(field_mappings)} field mappings, page URL: {page.url}"
    )
    for m in field_mappings:
        activity.logger.debug(
            f"[fill]   mapping: {m.form_label!r} → mapped_to={m.mapped_to!r} "
            f"value={m.value[:60]!r} confidence={m.confidence:.2f}"
        )

    filler = GreenhouseFiller(page, form_schema=form_schema)

    # Screenshot before fill
    await registry.screenshot(input.run_id, "before_fill")

    await filler.fill(
        field_mappings,
        cover_letter_path=input.cover_letter_path,
        cover_letter_text=input.cover_letter_text,
    )
    activity.logger.info(f"[fill] All fields filled — taking pre-submit screenshot")

    # Screenshot after fill, before submit
    await registry.screenshot(input.run_id, "before_submit")

    activity.logger.info(f"[fill] Submitting form")
    confirmation_url, confirmation_text = await filler.submit(email=input.account_email)
    activity.logger.info(f"[fill] Submission result — URL: {confirmation_url}")

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
