"""
GET /applications       — paginated list: search, multi-value `outcome` filter, sort
GET /applications/stats — counts by outcome

Each row carries the run's `outcome` and `actions` from run_state.derive_outcome() and
allowed_actions() (docs/wiki/contracts/run-lifecycle.md §8). The frontend renders them and never
derives either from `status`.
"""
from datetime import datetime, timezone
from typing import get_args

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from yahbah.api.errors import api_error
from yahbah.db.models import ApplicationRun, JobPosting
from yahbah.db.session import get_session
from yahbah.run_state import Action, Outcome, RunStatus, SubmissionMode, allowed_actions, derive_outcome

router = APIRouter()


# ── Outcome in SQL ─────────────────────────────────────────────────────────
# derive_outcome() looks at the status, the submission mode and whether an email confirmed the run.
# Evaluating it over every combination gives the CASE expression the filter uses, so SQL and Python
# can't disagree. Mode classes: each known mode, NULL (legacy), and any other value.

_OTHER_MODE = "\x00other"
_CONFIRMED = datetime(2000, 1, 1, tzinfo=timezone.utc)
_KNOWN_MODES = get_args(SubmissionMode)


def _mode_is(mode):
    column = ApplicationRun.submission_mode
    if mode is None:
        return column.is_(None)
    if mode == _OTHER_MODE:
        return column.not_in(_KNOWN_MODES)
    return column == mode


def _outcome_case():
    whens = []
    for status in get_args(RunStatus):
        combos = {(mode, confirmed): derive_outcome(status, mode, _CONFIRMED if confirmed else None)
                  for mode in (*_KNOWN_MODES, None, _OTHER_MODE) for confirmed in (False, True)}
        if len(set(combos.values())) == 1:
            whens.append((ApplicationRun.status == status, next(iter(combos.values()))))
            continue
        for (mode, confirmed), outcome in combos.items():
            email = ApplicationRun.email_confirmed_at.is_not(None) if confirmed else ApplicationRun.email_confirmed_at.is_(None)
            whens.append((and_(ApplicationRun.status == status, _mode_is(mode), email), outcome))
    return case(*whens, else_=None)


OUTCOME_SQL = _outcome_case()
OUTCOMES: tuple[str, ...] = get_args(Outcome)


# ── Response models ────────────────────────────────────────────────────────

class ApplicationListItem(BaseModel):
    run_id: str
    job_url: str
    company: str | None
    title: str | None
    location: str | None
    company_website: str | None
    ats_type: str
    status: str
    outcome: Outcome
    actions: list[Action]
    # Stored columns stay plain strings, so one unexpected value can't fail the whole list.
    failure_reason: str | None  # run_state.FailureReason
    submission_mode: str | None  # run_state.SubmissionMode; None = a run from before the contract
    origin: str | None  # run_state.Origin
    current_state: str | None
    review_round: int | None  # 1, 2, ...: rounds after the first ask about new fields
    match_score: int | None
    error_message: str | None
    submit_attempted_at: str | None
    review_deadline: str | None
    retry_of_run_id: str | None
    email_confirmed_at: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


class ApplicationListResponse(BaseModel):
    items: list[ApplicationListItem]
    total: int
    page: int
    per_page: int


class ApplicationStatsResponse(BaseModel):
    counts: dict[Outcome, int]  # every outcome, zeros included
    total: int


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _item(r: ApplicationRun) -> ApplicationListItem:
    jp = r.job_posting
    return ApplicationListItem(
        run_id=str(r.id),
        job_url=r.job_url,
        company=jp.company if jp else None,
        title=jp.title if jp else None,
        location=jp.location if jp else None,
        company_website=jp.company_website if jp else None,
        ats_type=jp.ats_type if jp else "unknown",
        status=r.status,
        outcome=derive_outcome(r.status, r.submission_mode, r.email_confirmed_at),
        actions=allowed_actions(r.status, r.submission_mode, r.submit_attempted_at),
        failure_reason=r.failure_reason,
        submission_mode=r.submission_mode,
        origin=r.origin,
        current_state=r.current_state,
        review_round=r.review_round,
        match_score=r.match_score,
        error_message=r.error_message,
        submit_attempted_at=_iso(r.submit_attempted_at),
        review_deadline=_iso(r.review_deadline),
        retry_of_run_id=str(r.retry_of_run_id) if r.retry_of_run_id else None,
        email_confirmed_at=_iso(r.email_confirmed_at),
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
        completed_at=_iso(r.completed_at),
    )


def _outcome_filter(values: list[str] | None) -> list[str]:
    """`outcome` may repeat (?outcome=a&outcome=b) or be comma-separated."""
    wanted = [v.strip() for value in values or [] for v in value.split(",") if v.strip()]
    unknown = sorted(set(wanted) - set(OUTCOMES))
    if unknown:
        raise api_error(422, "invalid_outcome",
                        f"Unknown outcome {', '.join(unknown)}; expected any of {', '.join(OUTCOMES)}.")
    return wanted


_SORTS = {
    "created_at_asc": ApplicationRun.created_at.asc(),
    "created_at_desc": ApplicationRun.created_at.desc(),
    "updated_at_desc": ApplicationRun.updated_at.desc(),
    "updated_at_asc": ApplicationRun.updated_at.asc(),
    "company_asc": JobPosting.company.asc(),
    "company_desc": JobPosting.company.desc(),
    "title_asc": JobPosting.title.asc(),
    "title_desc": JobPosting.title.desc(),
    "match_score_asc": ApplicationRun.match_score.asc(),
    "match_score_desc": ApplicationRun.match_score.desc(),
    "review_deadline_asc": ApplicationRun.review_deadline.asc().nulls_last(),  # the review queue: soonest first
}


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("", response_model=ApplicationListResponse)
async def list_applications(
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    outcome: list[str] | None = Query(None, description="Any of these outcomes; repeat or comma-separate"),
    search: str | None = Query(None),
    sort: str = Query("created_at_desc"),
    session: AsyncSession = Depends(get_session),
) -> ApplicationListResponse:
    base = select(ApplicationRun).join(
        JobPosting, ApplicationRun.job_posting_id == JobPosting.id
    ).options(selectinload(ApplicationRun.job_posting))

    if outcomes := _outcome_filter(outcome):
        base = base.where(OUTCOME_SQL.in_(outcomes))
    if search:
        term = f"%{search}%"
        base = base.where(
            or_(
                JobPosting.title.ilike(term),
                JobPosting.company.ilike(term),
                ApplicationRun.job_url.ilike(term),
            )
        )

    count_q = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_q)).scalar_one()

    order = _SORTS.get(sort, ApplicationRun.created_at.desc())
    base = base.order_by(order, ApplicationRun.created_at.desc(), ApplicationRun.id)
    base = base.offset((page - 1) * per_page).limit(per_page)

    runs = (await session.execute(base)).scalars().all()
    return ApplicationListResponse(items=[_item(r) for r in runs], total=total, page=page, per_page=per_page)


@router.get("/stats", response_model=ApplicationStatsResponse)
async def application_stats(
    session: AsyncSession = Depends(get_session),
) -> ApplicationStatsResponse:
    confirmed = ApplicationRun.email_confirmed_at.is_not(None)
    rows = await session.execute(
        select(ApplicationRun.status, ApplicationRun.submission_mode, confirmed, func.count())
        .group_by(ApplicationRun.status, ApplicationRun.submission_mode, confirmed)
    )
    counts = dict.fromkeys(OUTCOMES, 0)
    for status, mode, is_confirmed, n in rows.all():
        counts[derive_outcome(status, mode, _CONFIRMED if is_confirmed else None)] += n
    return ApplicationStatsResponse(counts=counts, total=sum(counts.values()))
