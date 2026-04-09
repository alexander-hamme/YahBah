"""
ApplicationWorkflow — one instance per application_run.

State machine:
  AUTH_CHECK → EXTRACT_FORM → MAP_FIELDS → GENERATE_COVER_LETTER
  → FILL_AND_SUBMIT → DONE
"""
import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from yahbah.schemas import (
        AuthInput,
        BrowserExtractInput,
        BrowserFillInput,
        CoverLetterInput,
        DuplicateCheckInput,
        JobMetadataInput,
        MapFieldsInput,
    )
    from yahbah.workflows.activities.browser import (
        browser_open_and_auth_activity,
        browser_extract_activity,
        browser_fill_and_submit_activity,
    )
    from yahbah.workflows.activities.llm import (
        extract_job_metadata_activity,
        generate_cover_letter_activity,
        map_fields_activity,
    )
    from yahbah.workflows.activities.db_ops import (
        check_duplicate_activity,
        mark_run_completed_activity,
        mark_run_duplicate_activity,
        mark_run_failed_activity,
        update_run_state_activity,
    )


@dataclass
class ApplicationWorkflowInput:
    run_id: str
    job_url: str


@workflow.defn
class ApplicationWorkflow:
    @workflow.run
    async def run(self, input: ApplicationWorkflowInput) -> None:
        run_id = input.run_id
        job_url = input.job_url

        # Shared timeout and retry config
        short = timedelta(minutes=2)
        medium = timedelta(minutes=5)
        long = timedelta(minutes=10)
        retry = RetryPolicy(maximum_attempts=2)

        # TODO more retries for initial stage, less for final stage?

        try:
            # ── AUTH_CHECK (open page + handle auth wall if present) ──────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "AUTH_CHECK"],
                start_to_close_timeout=short,
                retry_policy=retry,
            )
            auth_output = await workflow.execute_activity(
                browser_open_and_auth_activity,
                AuthInput(run_id=run_id, job_url=job_url),
                start_to_close_timeout=long,
                retry_policy=retry,
            )

            # ── EXTRACT_FORM ─────────────────────────────────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "EXTRACT_FORM"],
                start_to_close_timeout=short,
                retry_policy=retry,
            )
            extract_output = await workflow.execute_activity(
                browser_extract_activity,
                BrowserExtractInput(run_id=run_id, job_url=job_url),
                start_to_close_timeout=long,
                retry_policy=retry,
            )

            # ── Guard: no form fields ────────────────────────────────────────
            form_fields = extract_output.form_schema_dict.get("fields", [])
            if not form_fields:
                reason = "No form fields found — the application form may not have loaded correctly"
                await workflow.execute_activity(
                    mark_run_failed_activity,
                    args=[run_id, reason],
                    start_to_close_timeout=short,
                    retry_policy=retry,
                )
                raise RuntimeError(reason)

            # ── EXTRACT_METADATA (LLM) ───────────────────────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "EXTRACT_METADATA"],
                start_to_close_timeout=short,
                retry_policy=retry,
            )
            metadata_output = await workflow.execute_activity(
                extract_job_metadata_activity,
                JobMetadataInput(
                    run_id=run_id,
                    job_description=extract_output.job_description,
                ),
                start_to_close_timeout=medium,
                retry_policy=retry,
            )

            # ── CHECK_DUPLICATE ──────────────────────────────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "CHECK_DUPLICATE"],
                start_to_close_timeout=short,
                retry_policy=retry,
            )
            # DOM-scraped values take priority; LLM fills in blanks
            dup_result = await workflow.execute_activity(
                check_duplicate_activity,
                DuplicateCheckInput(
                    run_id=run_id,
                    canonical_url=extract_output.canonical_url,
                    job_title=extract_output.job_title or metadata_output.title,
                    job_company=extract_output.job_company or metadata_output.company,
                    job_location=extract_output.job_location or metadata_output.location,
                    job_description=metadata_output.description_summary,
                    salary_min=metadata_output.salary_min,
                    salary_max=metadata_output.salary_max,
                    company_website=metadata_output.company_website,
                    industry=metadata_output.industry,
                    company_description=metadata_output.company_description,
                    work_model=metadata_output.work_model,
                    seniority_level=metadata_output.seniority_level,
                    experience_required=metadata_output.experience_required,
                    posted_date=extract_output.posted_date or metadata_output.posted_date,
                    technologies=metadata_output.technologies,
                    specialties=metadata_output.specialties,
                ),
                start_to_close_timeout=short,
                retry_policy=retry,
            )
            if dup_result.is_duplicate:
                await workflow.execute_activity(
                    mark_run_duplicate_activity,
                    args=[run_id, dup_result.reason or "Duplicate job posting"],
                    start_to_close_timeout=short,
                    retry_policy=retry,
                )
                raise asyncio.CancelledError(dup_result.reason or "Duplicate job posting")

            # ── MAP_FIELDS (LLM) ─────────────────────────────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "MAP_FIELDS"],
                start_to_close_timeout=short,
                retry_policy=retry,
            )
            map_output = await workflow.execute_activity(
                map_fields_activity,
                MapFieldsInput(
                    run_id=run_id,
                    form_schema_dict=extract_output.form_schema_dict,
                    job_description=extract_output.job_description,
                    account_email=auth_output.account_email,
                ),
                start_to_close_timeout=medium,
                retry_policy=retry,
            )

            # ── Guard: no field mappings ──────────────────────────────────────
            if not map_output.field_mappings:
                reason = "No fields could be mapped — the form may be incompatible"
                await workflow.execute_activity(
                    mark_run_failed_activity,
                    args=[run_id, reason],
                    start_to_close_timeout=short,
                    retry_policy=retry,
                )
                raise RuntimeError(reason)

            # ── GENERATE_COVER_LETTER (LLM) ──────────────────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "GENERATE_COVER_LETTER"],
                start_to_close_timeout=short,
                retry_policy=retry,
            )
            cover_output = await workflow.execute_activity(
                generate_cover_letter_activity,
                CoverLetterInput(
                    run_id=run_id,
                    job_description=extract_output.job_description,
                ),
                start_to_close_timeout=medium,
                retry_policy=retry,
            )

            # ── FILL_FIELDS + UPLOAD + SUBMIT + CONFIRM ───────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "FILL_AND_SUBMIT"],
                start_to_close_timeout=short,
                retry_policy=retry,
            )
            await workflow.execute_activity(
                browser_fill_and_submit_activity,
                BrowserFillInput(
                    run_id=run_id,
                    job_url=job_url,
                    field_mappings=map_output.field_mappings,
                    form_schema_dict=extract_output.form_schema_dict,
                    cover_letter_path=cover_output.cover_letter_path,
                    cover_letter_text=cover_output.cover_letter_text,
                    account_email=auth_output.account_email,
                ),
                start_to_close_timeout=long,
                retry_policy=retry,
            )

            # ── DONE ─────────────────────────────────────────────────────────
            await workflow.execute_activity(
                mark_run_completed_activity,
                args=[run_id],
                start_to_close_timeout=short,
                retry_policy=retry,
            )

        except ActivityError as exc:
            await workflow.execute_activity(
                mark_run_failed_activity,
                args=[run_id, str(exc.cause or exc)],
                start_to_close_timeout=short,
                retry_policy=retry,
            )
            raise
