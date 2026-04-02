"""
GET /applications       — paginated list with filtering/search
GET /applications/stats — summary counts by status
"""
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from yahbah.db.models import ApplicationRun, JobPosting
from yahbah.db.session import get_session

router = APIRouter()


# ── Response models ────────────────────────────────────────────────────────

class ApplicationListItem(BaseModel):
    run_id: str
    job_url: str
    company: str | None
    title: str | None
    location: str | None
    ats_type: str
    status: str
    current_state: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


class ApplicationListResponse(BaseModel):
    items: list[ApplicationListItem]
    total: int
    page: int
    per_page: int


class ApplicationStatsResponse(BaseModel):
    pending: int
    running: int
    completed: int
    failed: int
    duplicate: int
    total: int


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("", response_model=ApplicationListResponse)
async def list_applications(
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    status: str | None = Query(None),
    search: str | None = Query(None),
    sort: str = Query("created_at_desc"),
    session: AsyncSession = Depends(get_session),
) -> ApplicationListResponse:
    base = select(ApplicationRun).join(
        JobPosting, ApplicationRun.job_posting_id == JobPosting.id
    ).options(selectinload(ApplicationRun.job_posting))

    # Filters
    if status:
        base = base.where(ApplicationRun.status == status.upper())
    if search:
        term = f"%{search}%"
        base = base.where(
            or_(
                JobPosting.title.ilike(term),
                JobPosting.company.ilike(term),
                ApplicationRun.job_url.ilike(term),
            )
        )

    # Count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_q)).scalar_one()

    # Sort
    if sort == "created_at_asc":
        base = base.order_by(ApplicationRun.created_at.asc())
    elif sort == "updated_at_desc":
        base = base.order_by(ApplicationRun.updated_at.desc())
    else:
        base = base.order_by(ApplicationRun.created_at.desc())

    # Paginate
    base = base.offset((page - 1) * per_page).limit(per_page)

    result = await session.execute(base)
    runs = result.scalars().all()

    return ApplicationListResponse(
        items=[
            ApplicationListItem(
                run_id=str(r.id),
                job_url=r.job_url,
                company=r.job_posting.company if r.job_posting else None,
                title=r.job_posting.title if r.job_posting else None,
                location=r.job_posting.location if r.job_posting else None,
                ats_type=r.job_posting.ats_type if r.job_posting else "unknown",
                status=r.status,
                current_state=r.current_state,
                error_message=r.error_message,
                created_at=r.created_at.isoformat(),
                updated_at=r.updated_at.isoformat(),
                completed_at=r.completed_at.isoformat() if r.completed_at else None,
            )
            for r in runs
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/stats", response_model=ApplicationStatsResponse)
async def application_stats(
    session: AsyncSession = Depends(get_session),
) -> ApplicationStatsResponse:
    result = await session.execute(
        select(ApplicationRun.status, func.count())
        .group_by(ApplicationRun.status)
    )
    counts = {row[0]: row[1] for row in result.all()}

    return ApplicationStatsResponse(
        pending=counts.get("PENDING", 0),
        running=counts.get("RUNNING", 0),
        completed=counts.get("COMPLETED", 0),
        failed=counts.get("FAILED", 0),
        duplicate=counts.get("DUPLICATE", 0),
        total=sum(counts.values()),
    )
