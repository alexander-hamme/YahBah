"""
ApplicationWorkflow — one instance per application_run.

State machine:
  AUTH_CHECK → EXTRACT_FORM → MAP_FIELDS → GENERATE_COVER_LETTER
  → FILL_AND_SUBMIT → DONE
"""
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from yahbah.schemas import (
        AuthInput,
        BrowserExtractInput,
        BrowserFillInput,
        CoverLetterInput,
        MapFieldsInput,
    )
    from yahbah.workflows.activities.browser import (
        browser_open_and_auth_activity,
        browser_extract_activity,
        browser_fill_and_submit_activity,
    )
    from yahbah.workflows.activities.llm import (
        generate_cover_letter_activity,
        map_fields_activity,
    )
    from yahbah.workflows.activities.db_ops import (
        mark_run_failed_activity,
        mark_run_completed_activity,
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

        # Shared timeout config
        short = timedelta(minutes=2)
        medium = timedelta(minutes=5)
        long = timedelta(minutes=10)

        try:
            # ── AUTH_CHECK (open page + handle auth wall if present) ──────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "AUTH_CHECK"],
                start_to_close_timeout=short,
            )
            await workflow.execute_activity(
                browser_open_and_auth_activity,
                AuthInput(run_id=run_id, job_url=job_url),
                start_to_close_timeout=long,
            )

            # ── EXTRACT_FORM ─────────────────────────────────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "EXTRACT_FORM"],
                start_to_close_timeout=short,
            )
            extract_output = await workflow.execute_activity(
                browser_extract_activity,
                BrowserExtractInput(run_id=run_id, job_url=job_url),
                start_to_close_timeout=long,
            )

            # ── MAP_FIELDS (LLM) ─────────────────────────────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "MAP_FIELDS"],
                start_to_close_timeout=short,
            )
            map_output = await workflow.execute_activity(
                map_fields_activity,
                MapFieldsInput(
                    run_id=run_id,
                    form_schema_dict=extract_output.form_schema_dict,
                    job_description=extract_output.job_description,
                ),
                start_to_close_timeout=medium,
            )

            # ── GENERATE_COVER_LETTER (LLM) ──────────────────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "GENERATE_COVER_LETTER"],
                start_to_close_timeout=short,
            )
            cover_output = await workflow.execute_activity(
                generate_cover_letter_activity,
                CoverLetterInput(
                    run_id=run_id,
                    job_description=extract_output.job_description,
                ),
                start_to_close_timeout=medium,
            )

            # ── FILL_FIELDS + UPLOAD + SUBMIT + CONFIRM ───────────────────────
            await workflow.execute_activity(
                update_run_state_activity,
                args=[run_id, "FILL_AND_SUBMIT"],
                start_to_close_timeout=short,
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
                ),
                start_to_close_timeout=long,
            )

            # ── DONE ─────────────────────────────────────────────────────────
            await workflow.execute_activity(
                mark_run_completed_activity,
                args=[run_id],
                start_to_close_timeout=short,
            )

        except ActivityError as exc:
            await workflow.execute_activity(
                mark_run_failed_activity,
                args=[run_id, str(exc.cause or exc)],
                start_to_close_timeout=short,
            )
            raise
