"""
POST /jobs — enqueue a new application run.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yahbah.config import settings
from yahbah.db.models import ApplicationRun, JobPosting
from yahbah.db.session import get_session
from yahbah.workflows.application import ApplicationWorkflow, ApplicationWorkflowInput

router = APIRouter()


class EnqueueJobRequest(BaseModel):
    job_url: str  # Greenhouse application URL


class EnqueueJobResponse(BaseModel):
    run_id: str
    status: str
    job_url: str


@router.post("", response_model=EnqueueJobResponse, status_code=201)
async def enqueue_job(
    request: Request,
    body: EnqueueJobRequest,
    session: AsyncSession = Depends(get_session),
) -> EnqueueJobResponse:
    now = datetime.now(timezone.utc)

    # Upsert JobPosting (idempotent by URL)
    result = await session.execute(
        select(JobPosting).where(JobPosting.url == body.job_url)
    )
    job_posting = result.scalar_one_or_none()
    if job_posting is None:
        job_posting = JobPosting(
            id=uuid.uuid4(),
            url=body.job_url,
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
        job_url=body.job_url,
        status="PENDING",
        temporal_workflow_id=workflow_id,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    await session.commit()

    # Kick off Temporal workflow using the shared client from app lifespan
    try:
        temporal = request.app.state.temporal
        await temporal.start_workflow(
            ApplicationWorkflow.run,
            ApplicationWorkflowInput(run_id=str(run_id), job_url=body.job_url),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
    except Exception as exc:
        # Mark run failed if we can't reach Temporal
        run.status = "FAILED"
        run.error_message = f"Failed to start workflow: {exc}"
        await session.commit()
        raise HTTPException(status_code=503, detail=f"Temporal unavailable: {exc}") from exc

    return EnqueueJobResponse(
        run_id=str(run_id),
        status="PENDING",
        job_url=body.job_url,
    )
