"""
ApplicationWorkflow — one instance per application_run.

State machine (current_state):
  QUEUED → DISCOVER → MAP_FORM → EXTRACT_METADATA → CHECK_DUPLICATE → MAP_ANSWERS
  → GENERATE_COVER_LETTER → FILL → SUBMIT → DONE

QUEUED waits for a run slot (settings.max_concurrent_runs). The browser stages run
yahbah.agent (code first, models as fallbacks); testing mode ends the run after FILL.
"""
import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from yahbah.schemas import (
        CoverLetterInput,
        DiscoverInput,
        DuplicateCheckInput,
        FillInput,
        JobMetadataInput,
        MapAnswersInput,
        MapFormInput,
        SubmitInput,
    )
    from yahbah.workflows.activities.browser import (
        close_session_activity,
        discover_activity,
        fill_activity,
        map_form_activity,
        submit_activity,
    )
    from yahbah.workflows.activities.llm import (
        extract_job_metadata_activity,
        generate_cover_letter_activity,
        map_answers_activity,
    )
    from yahbah.workflows.activities.db_ops import (
        acquire_run_slot_activity,
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

        short = timedelta(minutes=2)
        medium = timedelta(minutes=10)
        # Local models take 7-150 s per call; agent tiers can take many steps.
        agentic = timedelta(minutes=45)
        retry = RetryPolicy(maximum_attempts=2)

        async def state(name: str) -> None:
            await workflow.execute_activity(
                update_run_state_activity, args=[run_id, name],
                start_to_close_timeout=short, retry_policy=retry,
            )

        try:
            # ── QUEUED: wait for a run slot ───────────────────────────────────
            await workflow.execute_activity(
                acquire_run_slot_activity, run_id,
                start_to_close_timeout=timedelta(days=7), heartbeat_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=0),  # keep waiting across worker restarts
            )

            # ── DISCOVER: open the URL, reach the application form ────────────
            await state("DISCOVER")
            discovered = await workflow.execute_activity(
                discover_activity, DiscoverInput(run_id=run_id, job_url=job_url),
                start_to_close_timeout=agentic, retry_policy=retry,
            )

            # ── MAP_FORM: every field, tagged with a key ──────────────────────
            await state("MAP_FORM")
            form = await workflow.execute_activity(
                map_form_activity, MapFormInput(run_id=run_id, job_url=job_url),
                start_to_close_timeout=agentic, retry_policy=retry,
            )

            # ── EXTRACT_METADATA (driver) ─────────────────────────────────────
            await state("EXTRACT_METADATA")
            metadata = await workflow.execute_activity(
                extract_job_metadata_activity,
                JobMetadataInput(run_id=run_id, job_description=discovered.job_description),
                start_to_close_timeout=medium, retry_policy=retry,
            )

            # ── CHECK_DUPLICATE ───────────────────────────────────────────────
            await state("CHECK_DUPLICATE")
            # Structured page data (JSON-LD) takes priority; the model fills in blanks
            dup = await workflow.execute_activity(
                check_duplicate_activity,
                DuplicateCheckInput(
                    run_id=run_id,
                    canonical_url=discovered.canonical_url,
                    job_title=discovered.job_title or metadata.title,
                    job_company=discovered.job_company or metadata.company,
                    job_location=discovered.job_location or metadata.location,
                    job_description=metadata.description_summary,
                    salary_min=metadata.salary_min,
                    salary_max=metadata.salary_max,
                    company_website=metadata.company_website,
                    industry=metadata.industry,
                    company_description=metadata.company_description,
                    work_model=metadata.work_model,
                    seniority_level=metadata.seniority_level,
                    experience_required=metadata.experience_required,
                    posted_date=discovered.posted_date or metadata.posted_date,
                    technologies=metadata.technologies,
                    specialties=metadata.specialties,
                    ats_type=discovered.board,
                ),
                start_to_close_timeout=short, retry_policy=retry,
            )
            if dup.is_duplicate:
                await workflow.execute_activity(
                    mark_run_duplicate_activity, args=[run_id, dup.reason or "Duplicate job posting"],
                    start_to_close_timeout=short, retry_policy=retry,
                )
                await workflow.execute_activity(
                    close_session_activity, run_id, start_to_close_timeout=short, retry_policy=retry)
                raise asyncio.CancelledError(dup.reason or "Duplicate job posting")

            # ── MAP_ANSWERS (driver + prose) ──────────────────────────────────
            await state("MAP_ANSWERS")
            answers = await workflow.execute_activity(
                map_answers_activity,
                MapAnswersInput(
                    run_id=run_id,
                    form_schema_dict=form.form_schema_dict,
                    job_description=discovered.job_description,
                    account_email=discovered.account_email,
                ),
                start_to_close_timeout=agentic, retry_policy=retry,
            )

            # ── GENERATE_COVER_LETTER (prose) ─────────────────────────────────
            await state("GENERATE_COVER_LETTER")
            cover = await workflow.execute_activity(
                generate_cover_letter_activity,
                CoverLetterInput(run_id=run_id, job_description=discovered.job_description),
                start_to_close_timeout=medium, retry_policy=retry,
            )

            # ── FILL ──────────────────────────────────────────────────────────
            await state("FILL")
            filled = await workflow.execute_activity(
                fill_activity,
                FillInput(
                    run_id=run_id,
                    job_url=job_url,
                    form_schema_dict=form.form_schema_dict,
                    field_mappings=answers.field_mappings,
                    job_description=discovered.job_description,
                    cover_letter_path=cover.cover_letter_path,
                    cover_letter_text=cover.cover_letter_text,
                    account_email=discovered.account_email,
                ),
                start_to_close_timeout=agentic, retry_policy=retry,
            )

            # ── SUBMIT (unless testing mode) ──────────────────────────────────
            if not filled.testing_mode:
                await state("SUBMIT")
                await workflow.execute_activity(
                    submit_activity,
                    SubmitInput(
                        run_id=run_id,
                        job_url=job_url,
                        form_schema_dict=filled.form_schema_dict,
                        field_mappings=filled.field_mappings,
                        written=filled.written,
                        job_description=discovered.job_description,
                        cover_letter_path=cover.cover_letter_path,
                        cover_letter_text=cover.cover_letter_text,
                        account_email=discovered.account_email,
                    ),
                    # A retry only verifies (submit_attempted_at): it never clicks submit again blind.
                    start_to_close_timeout=agentic, retry_policy=retry,
                )

            # ── DONE ──────────────────────────────────────────────────────────
            await workflow.execute_activity(
                mark_run_completed_activity, args=[run_id],
                start_to_close_timeout=short, retry_policy=retry,
            )

        except ActivityError as exc:
            await workflow.execute_activity(
                mark_run_failed_activity, args=[run_id, str(exc.cause or exc)],
                start_to_close_timeout=short, retry_policy=retry,
            )
            await workflow.execute_activity(
                close_session_activity, run_id, start_to_close_timeout=short, retry_policy=retry)
            raise
