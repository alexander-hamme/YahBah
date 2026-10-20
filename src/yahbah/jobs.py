"""
Shared job submission logic.

submit_job_for_application() is the one way a run is created: it upserts a JobPosting, checks
that nothing blocks a new application to it (run_state.possibly_applied, contract §5), creates
the ApplicationRun with the current submission mode, and starts its Temporal workflow. Used by
POST /jobs, retry (POST /runs/{id}/retry), and scheduled-job promotion (poller and API).

The caller is responsible for normalizing the URL before calling this
function (via ``normalize_job_url()``). The URL intake policy is checked
again here, so a stored URL (e.g. a scheduled row) can't reach a workflow
unchecked.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from yahbah.config import get_effective_runtime_settings, settings
from yahbah.db.models import ApplicationRun, JobPosting
from yahbah.db.session import AsyncSessionLocal
from yahbah.run_state import IN_FLIGHT, Blocker, Origin, possibly_applied_url
from yahbah.url_utils import validate_job_url
from yahbah.workflows.application import ApplicationWorkflow, ApplicationWorkflowInput

ACTIVE_RUN_INDEX = "uq_application_run_active_posting"  # one active run per posting (contract §7)


class AlreadyApplied(Exception):
    """possibly_applied() found a run (or claim) that blocks a new application to the posting."""

    def __init__(self, blocker: Blocker) -> None:
        self.blocker = blocker
        super().__init__(f"already applied (or possibly applied): run {blocker.run_id} ({blocker.reason})")


class RunInFlight(Exception):
    """The posting already has an active run (e.g. a testing run, which possibly_applied() ignores)."""

    def __init__(self, run_id: uuid.UUID | None) -> None:
        self.run_id = run_id
        super().__init__(f"the posting already has an active run ({run_id})")


async def submit_job_for_application(
    job_url: str,
    temporal_client,
    *,
    origin: Origin = "manual",
    retry_of_run_id: uuid.UUID | None = None,
) -> str:
    """Upsert JobPosting, create ApplicationRun, start Temporal workflow.

    *job_url* must already be normalized.  Returns the run_id (str).
    Raises UnsafeURLError (before writing anything) if the URL breaks the
    intake policy, AlreadyApplied or RunInFlight if the posting can't take a
    new run, and re-raises a Temporal failure (the unstarted run is removed).
    """
    await validate_job_url(job_url)
    runtime = await get_effective_runtime_settings()  # snapshotted into the run (contract §3)
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        # Nothing may block the job under any posting: by URL, canonical URL, or the URL's canonical form.
        if blocker := await possibly_applied_url(session, job_url):
            raise AlreadyApplied(blocker)

        # Upsert JobPosting (idempotent by normalized URL)
        result = await session.execute(
            select(JobPosting).where(JobPosting.url == job_url)
        )
        job_posting = result.scalar_one_or_none()
        if job_posting is None:
            job_posting = JobPosting(
                id=uuid.uuid4(),
                url=job_url,
                ats_type="unknown",  # set to the detected job board once the form is found
                created_at=now,
            )
            session.add(job_posting)
            await session.flush()

        run_id = uuid.uuid4()
        workflow_id = f"application-{run_id}"
        session.add(ApplicationRun(
            id=run_id,
            job_posting_id=job_posting.id,
            job_url=job_url,
            status="PENDING",
            submission_mode=runtime["submission_mode"],
            origin=origin,
            retry_of_run_id=retry_of_run_id,
            temporal_workflow_id=workflow_id,
            created_at=now,
            updated_at=now,
        ))
        posting_id = job_posting.id
        try:
            await session.commit()
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != ACTIVE_RUN_INDEX:
                raise
            await session.rollback()
            raise RunInFlight(await _active_run(posting_id)) from exc

    try:
        await temporal_client.start_workflow(
            ApplicationWorkflow.run,
            ApplicationWorkflowInput(
                run_id=str(run_id),
                job_url=job_url,
                submission_mode=runtime["submission_mode"],
                origin=origin,
                review_window_hours=runtime["review_window_hours"],
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
    except BaseException:
        # Never started: drop the row so it doesn't hold the posting's active-run slot forever.
        async with AsyncSessionLocal() as session:
            await session.execute(delete(ApplicationRun).where(ApplicationRun.id == run_id))
            await session.commit()
        raise

    return str(run_id)


async def _active_run(posting_id: uuid.UUID) -> uuid.UUID | None:
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            select(ApplicationRun.id).where(ApplicationRun.job_posting_id == posting_id,
                                            ApplicationRun.status.in_(IN_FLIGHT))
        )).scalar_one_or_none()
