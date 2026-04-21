"""
Shared job submission logic.

submit_job_for_application() is the core function that upserts a
JobPosting, creates an ApplicationRun, and kicks off the Temporal
workflow.  Used by both the API route (POST /jobs) and the alert
poller's promotion step.

The caller is responsible for normalizing the URL before calling this
function (via ``normalize_job_url()``).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from yahbah.config import settings
from yahbah.db.models import ApplicationRun, JobPosting
from yahbah.db.session import AsyncSessionLocal
from yahbah.workflows.application import ApplicationWorkflow, ApplicationWorkflowInput


async def submit_job_for_application(
    job_url: str,
    temporal_client,
) -> str:
    """Upsert JobPosting, create ApplicationRun, start Temporal workflow.

    *job_url* must already be normalized.  Returns the run_id (str).
    Raises on Temporal failure.
    """
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        # Upsert JobPosting (idempotent by normalized URL)
        result = await session.execute(
            select(JobPosting).where(JobPosting.url == job_url)
        )
        job_posting = result.scalar_one_or_none()
        if job_posting is None:
            job_posting = JobPosting(
                id=uuid.uuid4(),
                url=job_url,
                ats_type="greenhouse",
                created_at=now,
            )
            session.add(job_posting)
            await session.flush()

        # Create ApplicationRun
        run_id = uuid.uuid4()
        workflow_id = f"application-{run_id}"

        run = ApplicationRun(
            id=run_id,
            job_posting_id=job_posting.id,
            job_url=job_url,
            status="PENDING",
            temporal_workflow_id=workflow_id,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        await session.commit()

    # Start Temporal workflow
    await temporal_client.start_workflow(
        ApplicationWorkflow.run,
        ApplicationWorkflowInput(run_id=str(run_id), job_url=job_url),
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )

    return str(run_id)
