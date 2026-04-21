"""
POST   /scheduled               — manually add a job to the scheduled queue
GET    /scheduled               — paginated list with filtering/search
GET    /scheduled/stats         — summary counts by status
GET    /scheduled/{id}          — single scheduled job detail
POST   /scheduled/{id}/approve  — approve (still waits for promote_after)
POST   /scheduled/{id}/reject   — reject
POST   /scheduled/{id}/hold     — toggle hold flag
POST   /scheduled/{id}/promote  — immediately promote to active queue
DELETE /scheduled/{id}          — remove from queue
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yahbah.config import settings, get_runtime_settings
from yahbah.db.models import ApplicationRun, JobPosting, ScheduledJob
from yahbah.db.session import get_session
from yahbah.jobs import submit_job_for_application
from yahbah.url_utils import normalize_job_url

router = APIRouter()


# ── Response models ────────────────────────────────────────────────────────

class ScheduledJobItem(BaseModel):
    id: str
    job_url: str
    source: str
    title: str | None
    company: str | None
    location: str | None
    snippet: str | None
    match_score: int | None
    match_rationale: str | None
    status: str
    hold: bool
    promote_after: str
    created_at: str
    updated_at: str
    promoted_run_id: str | None


class ScheduledJobListResponse(BaseModel):
    items: list[ScheduledJobItem]
    total: int
    page: int
    per_page: int


class ScheduledStatsResponse(BaseModel):
    scheduled: int
    approved: int
    skipped: int
    promoted: int
    total: int


class AddScheduledRequest(BaseModel):
    job_url: str


class ActionResponse(BaseModel):
    success: bool
    message: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _sj_id(scheduled_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(scheduled_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid ID format")


def _item(sj: ScheduledJob) -> ScheduledJobItem:
    return ScheduledJobItem(
        id=str(sj.id),
        job_url=sj.job_url,
        source=sj.source,
        title=sj.title,
        company=sj.company,
        location=sj.location,
        snippet=sj.snippet,
        match_score=sj.match_score,
        match_rationale=sj.match_rationale,
        status=sj.status,
        hold=sj.hold,
        promote_after=sj.promote_after.isoformat(),
        created_at=sj.created_at.isoformat(),
        updated_at=sj.updated_at.isoformat(),
        promoted_run_id=str(sj.promoted_run_id) if sj.promoted_run_id else None,
    )


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("", response_model=ScheduledJobItem, status_code=201)
async def add_scheduled(
    body: AddScheduledRequest,
    session: AsyncSession = Depends(get_session),
) -> ScheduledJobItem:
    """Manually add a job URL to the scheduled queue."""
    job_url = await normalize_job_url(body.job_url)

    # Dedup: already in scheduled queue?
    existing = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.job_url == job_url,
            ScheduledJob.status.notin_(["SKIPPED", "EXPIRED"]),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Job already in scheduled queue")

    # Dedup: already applied to?
    existing = await session.execute(
        select(ApplicationRun.id)
        .join(JobPosting)
        .where(
            (JobPosting.url == job_url) | (JobPosting.canonical_url == job_url)
        )
        .where(ApplicationRun.status.notin_(["FAILED"]))
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Job already applied to")

    rt = await get_runtime_settings()
    delay_hours = rt.get(
        "job_alert_promotion_delay_hours",
        settings.job_alert_promotion_delay_hours,
    )
    now = datetime.now(timezone.utc)

    sj = ScheduledJob(
        id=uuid.uuid4(),
        job_url=job_url,
        source="manual",
        gmail_message_id="",  # no email for manual adds
        promote_after=now + timedelta(hours=delay_hours),
    )
    session.add(sj)
    await session.commit()
    await session.refresh(sj)

    return _item(sj)


@router.get("", response_model=ScheduledJobListResponse)
async def list_scheduled(
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    status: str | None = Query(None),
    search: str | None = Query(None),
    sort: str = Query("created_at_desc"),
    session: AsyncSession = Depends(get_session),
) -> ScheduledJobListResponse:
    base = select(ScheduledJob)

    if status:
        base = base.where(ScheduledJob.status == status.upper())
    if search:
        term = f"%{search}%"
        base = base.where(
            or_(
                ScheduledJob.title.ilike(term),
                ScheduledJob.company.ilike(term),
                ScheduledJob.job_url.ilike(term),
                ScheduledJob.snippet.ilike(term),
            )
        )

    count_q = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_q)).scalar_one()

    sort_map = {
        "created_at_asc": ScheduledJob.created_at.asc(),
        "created_at_desc": ScheduledJob.created_at.desc(),
        "match_score_asc": ScheduledJob.match_score.asc(),
        "match_score_desc": ScheduledJob.match_score.desc(),
        "promote_after_asc": ScheduledJob.promote_after.asc(),
        "promote_after_desc": ScheduledJob.promote_after.desc(),
        "status_asc": ScheduledJob.status.asc(),
        "status_desc": ScheduledJob.status.desc(),
    }
    base = base.order_by(sort_map.get(sort, ScheduledJob.created_at.desc()))
    base = base.offset((page - 1) * per_page).limit(per_page)

    result = await session.execute(base)
    jobs = result.scalars().all()

    return ScheduledJobListResponse(
        items=[_item(sj) for sj in jobs],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/stats", response_model=ScheduledStatsResponse)
async def scheduled_stats(
    session: AsyncSession = Depends(get_session),
) -> ScheduledStatsResponse:
    result = await session.execute(
        select(ScheduledJob.status, func.count())
        .group_by(ScheduledJob.status)
    )
    counts = {row[0]: row[1] for row in result.all()}

    return ScheduledStatsResponse(
        scheduled=counts.get("SCHEDULED", 0),
        approved=counts.get("APPROVED", 0),
        skipped=counts.get("SKIPPED", 0),
        promoted=counts.get("PROMOTED", 0),
        total=sum(counts.values()),
    )


@router.get("/{scheduled_id}", response_model=ScheduledJobItem)
async def get_scheduled(
    scheduled_id: str,
    session: AsyncSession = Depends(get_session),
) -> ScheduledJobItem:
    uid = _sj_id(scheduled_id)
    sj = await session.get(ScheduledJob, uid)
    if sj is None:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    return _item(sj)


# ── Actions ────────────────────────────────────────────────────────────────

@router.post("/{scheduled_id}/approve", response_model=ActionResponse)
async def approve_scheduled(
    scheduled_id: str,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    uid = _sj_id(scheduled_id)
    sj = await session.get(ScheduledJob, uid)
    if sj is None:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    if sj.status not in ("SCHEDULED", "SKIPPED"):
        raise HTTPException(status_code=409, detail=f"Cannot approve job in {sj.status} state")
    sj.status = "APPROVED"
    sj.hold = False
    await session.commit()
    return ActionResponse(success=True, message="Job approved")


@router.post("/{scheduled_id}/reject", response_model=ActionResponse)
async def reject_scheduled(
    scheduled_id: str,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    uid = _sj_id(scheduled_id)
    sj = await session.get(ScheduledJob, uid)
    if sj is None:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    if sj.status in ("PROMOTED",):
        raise HTTPException(status_code=409, detail="Cannot reject an already-promoted job")
    sj.status = "SKIPPED"
    await session.commit()
    return ActionResponse(success=True, message="Job rejected")


@router.post("/{scheduled_id}/hold", response_model=ActionResponse)
async def toggle_hold(
    scheduled_id: str,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    uid = _sj_id(scheduled_id)
    sj = await session.get(ScheduledJob, uid)
    if sj is None:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    if sj.status not in ("SCHEDULED", "APPROVED"):
        raise HTTPException(status_code=409, detail=f"Cannot hold job in {sj.status} state")
    sj.hold = not sj.hold
    await session.commit()
    action = "held" if sj.hold else "released"
    return ActionResponse(success=True, message=f"Job {action}")


@router.post("/{scheduled_id}/promote", response_model=ActionResponse)
async def promote_scheduled(
    scheduled_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    uid = _sj_id(scheduled_id)
    sj = await session.get(ScheduledJob, uid)
    if sj is None:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    if sj.status == "PROMOTED":
        raise HTTPException(status_code=409, detail="Job already promoted")
    if sj.status not in ("SCHEDULED", "APPROVED"):
        raise HTTPException(status_code=409, detail=f"Cannot promote job in {sj.status} state")

    try:
        temporal = request.app.state.temporal
        run_id = await submit_job_for_application(sj.job_url, temporal)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to start workflow: {exc}") from exc

    sj.status = "PROMOTED"
    sj.promoted_run_id = uuid.UUID(run_id)
    await session.commit()

    return ActionResponse(success=True, message=f"Job promoted → run {run_id}")


@router.delete("/{scheduled_id}", response_model=ActionResponse)
async def delete_scheduled(
    scheduled_id: str,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    uid = _sj_id(scheduled_id)
    sj = await session.get(ScheduledJob, uid)
    if sj is None:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    await session.delete(sj)
    await session.commit()
    return ActionResponse(success=True, message="Scheduled job deleted")
