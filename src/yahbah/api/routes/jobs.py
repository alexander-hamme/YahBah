"""
POST /jobs — enqueue a new application run.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from yahbah.jobs import submit_job_for_application
from yahbah.url_utils import normalize_job_url

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
) -> EnqueueJobResponse:
    job_url = await normalize_job_url(body.job_url)

    try:
        temporal = request.app.state.temporal
        run_id = await submit_job_for_application(job_url, temporal)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Failed to start workflow: {exc}"
        ) from exc

    return EnqueueJobResponse(
        run_id=run_id,
        status="PENDING",
        job_url=job_url,
    )
