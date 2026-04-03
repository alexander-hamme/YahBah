"""
GET /runs/{id}                          — run status + current state
GET /runs/{id}/steps                    — all steps with logs
GET /runs/{id}/artifacts                — all artifacts
GET /runs/{id}/artifacts/{aid}/download — serve artifact file
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from yahbah.config import settings
from yahbah.db.models import ApplicationRun, ApplicationStep, ApplicationArtifact
from yahbah.db.session import get_session

router = APIRouter()


# ── Response models ────────────────────────────────────────────────────────

class JobPostingInfo(BaseModel):
    title: str | None
    company: str | None
    location: str | None
    salary_min: int | None
    salary_max: int | None
    company_website: str | None
    technologies: list[str] | None
    specialties: list[str] | None
    ats_type: str


class RunResponse(BaseModel):
    id: str
    job_url: str
    status: str
    current_state: str | None
    temporal_workflow_id: str | None
    account_email: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    job_posting: JobPostingInfo | None = None


class StepResponse(BaseModel):
    id: str
    step_name: str
    status: str
    logs: str | None
    started_at: str | None
    completed_at: str | None


class ArtifactResponse(BaseModel):
    id: str
    artifact_type: str
    path: str
    metadata: dict | None
    created_at: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _run_id(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> RunResponse:
    uid = _run_id(run_id)
    result = await session.execute(
        select(ApplicationRun)
        .where(ApplicationRun.id == uid)
        .options(selectinload(ApplicationRun.job_posting))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    jp = run.job_posting
    return RunResponse(
        id=str(run.id),
        job_url=run.job_url,
        status=run.status,
        current_state=run.current_state,
        temporal_workflow_id=run.temporal_workflow_id,
        account_email=run.account_email,
        error_message=run.error_message,
        created_at=run.created_at.isoformat(),
        updated_at=run.updated_at.isoformat(),
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        job_posting=JobPostingInfo(
            title=jp.title,
            company=jp.company,
            location=jp.location,
            salary_min=jp.salary_min,
            salary_max=jp.salary_max,
            company_website=jp.company_website,
            technologies=jp.technologies,
            specialties=jp.specialties,
            ats_type=jp.ats_type,
        ) if jp else None,
    )


@router.get("/{run_id}/steps", response_model=list[StepResponse])
async def get_steps(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[StepResponse]:
    uid = _run_id(run_id)
    run = await session.get(ApplicationRun, uid)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    result = await session.execute(
        select(ApplicationStep)
        .where(ApplicationStep.run_id == uid)
        .order_by(ApplicationStep.created_at)
    )
    steps = result.scalars().all()

    return [
        StepResponse(
            id=str(s.id),
            step_name=s.step_name,
            status=s.status,
            logs=s.logs,
            started_at=s.started_at.isoformat() if s.started_at else None,
            completed_at=s.completed_at.isoformat() if s.completed_at else None,
        )
        for s in steps
    ]


@router.get("/{run_id}/artifacts", response_model=list[ArtifactResponse])
async def get_artifacts(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[ArtifactResponse]:
    uid = _run_id(run_id)
    run = await session.get(ApplicationRun, uid)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    result = await session.execute(
        select(ApplicationArtifact)
        .where(ApplicationArtifact.run_id == uid)
        .order_by(ApplicationArtifact.created_at)
    )
    artifacts = result.scalars().all()

    return [
        ArtifactResponse(
            id=str(a.id),
            artifact_type=a.artifact_type,
            path=a.path,
            metadata=a.artifact_metadata,
            created_at=a.created_at.isoformat(),
        )
        for a in artifacts
    ]


# ── Artifact content types ────────────────────────────────────────────────

_MEDIA_TYPES: dict[str, str] = {
    "screenshot": "image/png",
    "cover_letter": "application/pdf",
    "trace": "application/zip",
    "form_schema": "application/json",
    "field_mappings": "application/json",
    "submitted_values": "application/json",
    "confirmation": "text/plain",
}


@router.get("/{run_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    run_id: str,
    artifact_id: str,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    uid = _run_id(run_id)
    try:
        aid = uuid.UUID(artifact_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid artifact_id format")

    result = await session.execute(
        select(ApplicationArtifact).where(
            ApplicationArtifact.id == aid,
            ApplicationArtifact.run_id == uid,
        )
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    file_path = Path(artifact.path)
    if not file_path.is_absolute():
        file_path = file_path.resolve()

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file not found on disk")

    media_type = _MEDIA_TYPES.get(artifact.artifact_type, "application/octet-stream")
    return FileResponse(file_path, media_type=media_type, filename=file_path.name)
