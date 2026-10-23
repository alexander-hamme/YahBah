"""
ApplicationWorkflow — one instance per application_run (docs/wiki/contracts/run-lifecycle.md).

Stages (current_state):
  QUEUED → DISCOVER → MAP_FORM → EXTRACT_METADATA → CHECK_DUPLICATE → MAP_ANSWERS
  → GENERATE_COVER_LETTER (only if the form asks for one) → FILL, then by the run's submission_mode (snapshotted at creation):
  - testing: COMPLETED after FILL; nothing is submitted.
  - review:  REVIEW (AWAITING_REVIEW) until the user approves or rejects the current round or the
    window elapses (EXPIRED). An approval waits for a run slot, re-fills exactly the approved
    answers (FILL), and SUBMITs; a required field without an approved answer is mapped
    (MAP_ANSWERS) and sent back to REVIEW as a new round instead.

A run that DISCOVER moved to the headed fallback browser (DiscoverOutput.headed, contract §2) passes
headed=True to every later browser stage, the approved re-fill and SUBMIT included.

QUEUED waits for a run slot (settings.max_concurrent_runs); only RUNNING holds one. Every status
write is an activity fenced on this workflow's id (activities/db_ops.py). Cancellation closes the
browser and ends the run CANCELLED; a run whose submit was attempted ends SUBMIT_UNCERTAIN
whatever ends it.
"""
import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import get_args

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError, is_cancelled_exception
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from yahbah.llm.cover_letter import cover_letter_requested
    from yahbah.run_state import FailureReason
    from yahbah.schemas import (
        CoverLetterInput,
        DiscoverInput,
        DuplicateCheckInput,
        FillInput,
        JobMetadataInput,
        MapAnswersInput,
        MapFormInput,
        MapNewFieldsInput,
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
        map_new_fields_activity,
    )
    from yahbah.workflows.activities.db_ops import (
        acquire_run_slot_activity,
        check_duplicate_activity,
        enter_review_activity,
        finish_run_activity,
        mark_run_completed_activity,
        update_run_state_activity,
    )

# ── Timeouts and retries (module-level so tests can patch them) ──────────────────
SHORT = timedelta(minutes=2)          # DB writes, closing the browser
MEDIUM = timedelta(minutes=10)        # metadata extraction, cover letter
AGENTIC = timedelta(minutes=45)       # browser stages and answer mapping: local models take 7-150 s per call
HEARTBEAT = timedelta(minutes=2, seconds=30)  # long activities heartbeat in the background (heartbeat.py)
SLOT_WAIT = timedelta(days=7)
SLOT_HEARTBEAT = timedelta(minutes=2)
RETRY = RetryPolicy(maximum_attempts=2)
DB_RETRY = RetryPolicy(maximum_attempts=5)  # a lost terminal write would leave the run in flight

_REASONS = frozenset(get_args(FailureReason))
_BROWSER_ACTIVITIES = {"discover_activity", "map_form_activity", "fill_activity", "submit_activity"}


@dataclass
class ApplicationWorkflowInput:
    run_id: str
    job_url: str
    # Snapshotted at creation (contract §3, §4): later settings changes affect new runs only.
    submission_mode: str = "review"
    origin: str = "manual"
    review_window_hours: float = 48.0
    # Missing in inputs from before the setting existed: no headed fallback (contract §2).
    browser_headed_fallback: bool = False


def failure_reason(exc: ActivityError) -> str:
    """The run's failure_reason for an activity failure (contract §2)."""
    cause = exc.cause
    if isinstance(cause, TimeoutError):
        return "timeout"
    if isinstance(cause, ApplicationError):
        if cause.type in _REASONS:
            return cause.type
        if cause.type == "LLMError":
            return "llm_error"
    return "browser_error" if exc.activity_type in _BROWSER_ACTIVITIES else "internal_error"


@workflow.defn
class ApplicationWorkflow:
    def __init__(self) -> None:
        self._round = 0             # the current review round (contract §4)
        self._awaiting = False      # accepting a decision for self._round
        self._decision: tuple[str, str | None] | None = None  # ("approve" | "reject", reason)

    # ── review signals ────────────────────────────────────────────────────────
    @workflow.signal
    def approve_submission(self, review_round: int) -> None:
        self._decide(review_round, "approve", None)

    @workflow.signal
    def reject_submission(self, review_round: int, reason: str | None = None) -> None:
        self._decide(review_round, "reject", reason)

    def _decide(self, review_round: int, decision: str, reason: str | None) -> None:
        if not self._awaiting or review_round != self._round or self._decision is not None:
            workflow.logger.warning(f"ignoring {decision} for review round {review_round}: current round "
                                    f"{self._round}, {'awaiting' if self._awaiting else 'not awaiting'} a decision")
            return
        self._decision = (decision, reason)

    # ── run ───────────────────────────────────────────────────────────────────
    @workflow.run
    async def run(self, input: ApplicationWorkflowInput) -> str:
        self._run_id = input.run_id
        try:
            return await self._stages(input)
        except (asyncio.CancelledError, Exception) as exc:  # a cancel while waiting is asyncio's
            if is_cancelled_exception(exc):
                await self._close_browser()
                await self._end("CANCELLED", "cancelled", "Cancelled by user")
                raise
            if not isinstance(exc, ActivityError):
                raise  # a bug in this workflow: fail the task, Temporal retries it after a fix
            reason = failure_reason(exc)
            await self._close_browser()
            if reason != "superseded":  # a superseded run belongs to someone else: write nothing
                await self._end("FAILED", reason, str(exc.cause or exc)[:4000])
            raise

    async def _stages(self, input: ApplicationWorkflowInput) -> str:
        """Run every stage; returns the status the run ended with."""
        run_id, job_url = input.run_id, input.job_url

        # ── QUEUED: wait for a run slot ───────────────────────────────────────
        await self._acquire_slot()

        # ── DISCOVER: open the URL, reach the application form ────────────────
        await self._state("DISCOVER")
        discovered = await self._long(discover_activity, DiscoverInput(
            run_id=run_id, job_url=job_url, headed_fallback=input.browser_headed_fallback))
        # Headed for the rest of the run, across reviews (contract §2). From history, so replay keeps it.
        headed = discovered.headed

        # ── MAP_FORM: every field, tagged with a key ──────────────────────────
        await self._state("MAP_FORM")
        form = await self._long(map_form_activity, MapFormInput(run_id=run_id, job_url=job_url, headed=headed))

        # ── EXTRACT_METADATA (driver) ─────────────────────────────────────────
        await self._state("EXTRACT_METADATA")
        metadata = await workflow.execute_activity(
            extract_job_metadata_activity,
            JobMetadataInput(run_id=run_id, job_description=discovered.job_description),
            start_to_close_timeout=MEDIUM, retry_policy=RETRY,
        )

        # ── CHECK_DUPLICATE ───────────────────────────────────────────────────
        await self._state("CHECK_DUPLICATE")
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
            start_to_close_timeout=SHORT, retry_policy=RETRY,
        )
        if dup.is_duplicate:
            await self._close_browser()
            await self._end("DUPLICATE", "duplicate", dup.reason or "Duplicate job posting")
            return "DUPLICATE"

        # ── MAP_ANSWERS (driver + prose) ──────────────────────────────────────
        await self._state("MAP_ANSWERS")
        job_location = discovered.job_location or metadata.location  # scopes authorization questions
        answers = await self._long(map_answers_activity, MapAnswersInput(
            run_id=run_id,
            form_schema_dict=form.form_schema_dict,
            job_description=discovered.job_description,
            account_email=discovered.account_email,
            job_location=job_location,
        ))

        # ── GENERATE_COVER_LETTER (prose), only if the form asks for one ──────
        cover_path, cover_text = None, None
        if cover_letter_requested(answers.field_mappings):
            await self._state("GENERATE_COVER_LETTER")
            cover = await workflow.execute_activity(
                generate_cover_letter_activity,
                CoverLetterInput(run_id=run_id, job_description=discovered.job_description),
                start_to_close_timeout=MEDIUM, retry_policy=RETRY,
            )
            cover_path, cover_text = cover.cover_letter_path, cover.cover_letter_text

        def fill_input(mappings: list[dict], review_round: int, refill: bool) -> FillInput:
            return FillInput(
                run_id=run_id,
                job_url=job_url,
                form_schema_dict=form.form_schema_dict,
                field_mappings=mappings,
                job_description=discovered.job_description,
                cover_letter_path=cover_path,
                cover_letter_text=cover_text,
                account_email=discovered.account_email,
                job_location=job_location,
                review_round=review_round,
                refill=refill,
                headed=headed,
            )

        # ── FILL: evidence and screenshot saved, browser closed ───────────────
        await self._state("FILL")
        filled = await self._long(fill_activity, fill_input(answers.field_mappings, 1, refill=False))
        if input.submission_mode == "testing":
            await self._complete()
            return "COMPLETED"

        # ── REVIEW → (approved) FILL again → SUBMIT ───────────────────────────
        approved = filled.field_mappings
        while True:
            decision, reason = await self._review(input.review_window_hours)
            if decision == "expired":
                await self._end("EXPIRED", "review_expired",
                                f"Not approved within the {input.review_window_hours:g} h review window")
                return "EXPIRED"
            if decision == "reject":
                await self._end("CANCELLED", "review_rejected", reason or "Rejected in review")
                return "CANCELLED"

            await self._acquire_slot()
            await self._state("FILL")
            filled = await self._long(fill_activity, fill_input(approved, self._round + 1, refill=True))
            new_fields = [f for f in filled.unmapped_new_fields if f.get("required")]
            if not new_fields:
                break
            # A required field nobody approved: map it and review it (the browser is closed).
            await self._state("MAP_ANSWERS")
            mapped = await self._long(map_new_fields_activity, MapNewFieldsInput(
                run_id=run_id,
                fields=new_fields,
                approved_mappings=approved,
                job_description=discovered.job_description,
                review_round=self._round + 1,
                account_email=discovered.account_email,
                job_location=job_location,
            ))
            approved = list({m["key"]: m for m in approved + mapped.field_mappings}.values())

        await self._state("SUBMIT")
        await self._long(submit_activity, SubmitInput(
            run_id=run_id,
            job_url=job_url,
            form_schema_dict=filled.form_schema_dict,
            field_mappings=filled.field_mappings,
            written=filled.written,
            job_description=discovered.job_description,
            cover_letter_path=cover_path,
            cover_letter_text=cover_text,
            account_email=discovered.account_email,
            mapped_form_dict=form.form_schema_dict,
            baseline=filled.baseline,  # what the approved re-fill left in fields nobody approved
            headed=headed,
        ))
        await self._complete()
        return "COMPLETED"

    # ── helpers ───────────────────────────────────────────────────────────────
    async def _review(self, window_hours: float) -> tuple[str, str | None]:
        """Enter AWAITING_REVIEW for the next round; wait for its decision or the deadline."""
        self._round += 1
        self._decision = None
        self._awaiting = True  # before the DB write: the API signals as soon as it sees the round
        try:
            deadline = workflow.now() + timedelta(hours=window_hours)
            await workflow.execute_activity(
                enter_review_activity, args=[self._run_id, self._round, deadline.isoformat()],
                start_to_close_timeout=SHORT, retry_policy=DB_RETRY,
            )
            try:
                await workflow.wait_condition(lambda: self._decision is not None,
                                              timeout=deadline - workflow.now(),
                                              timeout_summary=f"review round {self._round}")
            except asyncio.TimeoutError:
                return ("expired", None)
        finally:
            self._awaiting = False
        return self._decision

    async def _long(self, activity_fn, arg):
        """A long activity: browser stages and answer mapping (contract §7)."""
        return await workflow.execute_activity(
            activity_fn, arg,
            start_to_close_timeout=AGENTIC, heartbeat_timeout=HEARTBEAT, retry_policy=RETRY,
            # Cleanup (closing the browser) waits until a cancelled activity has really stopped.
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    async def _acquire_slot(self) -> None:
        await workflow.execute_activity(
            acquire_run_slot_activity, self._run_id,
            start_to_close_timeout=SLOT_WAIT, heartbeat_timeout=SLOT_HEARTBEAT,
            retry_policy=RetryPolicy(maximum_attempts=0),  # keep waiting across worker restarts
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    async def _state(self, name: str) -> None:
        await workflow.execute_activity(
            update_run_state_activity, args=[self._run_id, name],
            start_to_close_timeout=SHORT, retry_policy=DB_RETRY,
        )

    async def _complete(self) -> None:
        await workflow.execute_activity(
            mark_run_completed_activity, self._run_id, start_to_close_timeout=SHORT, retry_policy=DB_RETRY)

    async def _end(self, status: str, reason: str, message: str | None) -> None:
        """The terminal write; a superseded run's is refused and ignored (it isn't ours any more)."""
        try:
            await workflow.execute_activity(
                finish_run_activity, args=[self._run_id, status, reason, message],
                start_to_close_timeout=SHORT, retry_policy=DB_RETRY,
            )
        except ActivityError as exc:
            if failure_reason(exc) != "superseded":
                raise
            workflow.logger.warning(f"run {self._run_id} was superseded; not writing {status}")

    async def _close_browser(self) -> None:
        await workflow.execute_activity(
            close_session_activity, self._run_id, start_to_close_timeout=SHORT, retry_policy=RETRY)
