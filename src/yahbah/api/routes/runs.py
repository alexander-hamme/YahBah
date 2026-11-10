"""
GET    /runs/{id}                          — run status, outcome, allowed actions
GET    /runs/{id}/review                   — review payload of the current (or last) review round
GET    /runs/{id}/steps                    — all steps with logs
GET    /runs/{id}/artifacts                — all artifacts
GET    /runs/{id}/artifacts/{aid}/download — serve artifact file (HTML as sandboxed plain text)
GET    /runs/{id}/status-updates           — classified emails for the run
POST   /runs/{id}/approve                  — approve the current review round (signals the workflow)
POST   /runs/{id}/reject                   — reject the current review round (signals the workflow)
POST   /runs/{id}/cancel                   — cancel through the workflow (reconciled if it is gone)
POST   /runs/{id}/retry                    — a new run linked to this one
POST   /runs/{id}/confirm-submitted        — resolve SUBMIT_UNCERTAIN as submitted
POST   /runs/{id}/confirm-not-submitted    — resolve SUBMIT_UNCERTAIN as not submitted (releases the job)
POST   /runs/bulk-delete                   — delete runs that allow it
DELETE /runs/{id}                          — delete a run that allows it

Routes never write a run's status (contract §1): approve, reject and cancel go through the workflow,
retry creates a new run. The exceptions are fenced on the expected status: resolving an uncertain
run, and reconciling an in-flight run whose workflow no longer exists. Which actions a run allows
comes from run_state.allowed_actions(), here and in the UI. Errors follow contract §8.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from temporalio.service import RPCError, RPCStatusCode

from yahbah.api.errors import api_error, enqueue_refused
from yahbah.db.models import (
    ApplicationArtifact, ApplicationRun, ApplicationStatusUpdate, ApplicationStep, SubmissionClaim,
)
from yahbah.db.session import get_session
from yahbah.jobs import AlreadyApplied, RunInFlight, submit_job_for_application
from yahbah.run_state import IN_FLIGHT, Action, allowed_actions, derive_outcome, posting_key

router = APIRouter()


# ── Response models ────────────────────────────────────────────────────────

class JobPostingInfo(BaseModel):
    title: str | None
    company: str | None
    location: str | None
    salary_min: int | None
    salary_max: int | None
    company_website: str | None
    industry: str | None
    company_description: str | None
    work_model: str | None
    seniority_level: str | None
    experience_required: str | None
    posted_date: str | None
    technologies: list[str] | None
    specialties: list[str] | None
    ats_type: str


class RunResponse(BaseModel):
    id: str
    job_url: str
    status: str
    outcome: str                  # run_state.derive_outcome(): what the UI shows
    actions: list[Action]         # run_state.allowed_actions(): what the UI offers
    current_state: str | None
    failure_reason: str | None
    submission_mode: str | None
    origin: str | None
    review_round: int | None
    review_deadline: str | None
    retry_of_run_id: str | None
    temporal_workflow_id: str | None
    account_email: str | None
    error_message: str | None
    match_score: int | None
    match_rationale: str | None
    tracking_url: str | None
    confirmation_html: str | None
    submit_attempted_at: str | None
    email_confirmed_at: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    job_posting: JobPostingInfo | None = None


class ReviewJob(BaseModel):
    title: str | None
    company: str | None
    url: str


class CoverLetterInfo(BaseModel):
    text: str | None
    artifact_id: str


class ReviewResponse(BaseModel):
    """What the user approves (contract §8): every answer as filled and read back, plus new fields."""
    run_id: str
    status: str
    submission_mode: str | None
    review_round: int
    review_deadline: str | None
    job: ReviewJob
    answers: list[dict]       # fill evidence rows (the round's submitted_values artifact)
    new_fields: list[dict]    # rows that appeared on a re-fill and need approval (same shape)
    screenshot_artifact_id: str | None
    cover_letter: CoverLetterInfo | None


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


class StatusUpdateResponse(BaseModel):
    id: str
    status_type: str
    subject: str | None
    sender: str | None
    summary: str | None
    email_date: str
    confidence: float
    created_at: str


class ActionResponse(BaseModel):
    success: bool
    message: str
    run_id: str | None = None


class ReviewDecision(BaseModel):
    review_round: int


class ReviewRejection(ReviewDecision):
    reason: str | None = None


class BulkDeleteRequest(BaseModel):
    run_ids: list[str] | None = None
    status: str | None = None


class BulkDeleteResponse(BaseModel):
    deleted: int
    skipped: int = 0  # runs whose actions don't include delete (in flight, or possibly submitted)
    success: bool


# ── Helpers ────────────────────────────────────────────────────────────────

def _run_id(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except ValueError:
        raise api_error(422, "invalid_run_id", "Invalid run_id format")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _actions(run: ApplicationRun) -> list[Action]:
    return allowed_actions(run.status, run.submission_mode, run.submit_attempted_at)


async def _load(session: AsyncSession, run_id: str) -> ApplicationRun:
    run = (await session.execute(
        select(ApplicationRun).where(ApplicationRun.id == _run_id(run_id))
        .options(selectinload(ApplicationRun.job_posting))
    )).scalar_one_or_none()
    if run is None:
        raise api_error(404, "run_not_found", "Run not found")
    return run


def _require(run: ApplicationRun, action: Action, code: str = "action_not_allowed") -> None:
    if action not in _actions(run):
        raise api_error(409, code, f"A {run.status} run can't {action.replace('_', ' ')}", run.id)


def _rpc_not_found(exc: BaseException) -> bool:
    return isinstance(exc, RPCError) and exc.status == RPCStatusCode.NOT_FOUND


async def _reconcile(session: AsyncSession, run: ApplicationRun) -> None:
    """The run is in flight but its workflow no longer exists: close it as cancelled, or as
    SUBMIT_UNCERTAIN if it may have been submitted (contract §7). Fenced on the in-flight status."""
    await session.execute(
        update(ApplicationRun)
        .where(ApplicationRun.id == run.id, ApplicationRun.status.in_(IN_FLIGHT))
        .values(status=case((ApplicationRun.submit_attempted_at.is_not(None), "SUBMIT_UNCERTAIN"),
                            else_="CANCELLED"),
                failure_reason="cancelled", error_message="Cancelled by user (its workflow no longer exists)",
                completed_at=func.now(), updated_at=func.now())
        .execution_options(synchronize_session=False))
    await session.commit()


def _temporal(request: Request):
    temporal = request.app.state.temporal
    if temporal is None:
        raise api_error(503, "temporal_unavailable", "Temporal client not available")
    return temporal


async def _signal(request: Request, session: AsyncSession, run: ApplicationRun, signal: str, args: list) -> None:
    try:
        await _temporal(request).get_workflow_handle(run.temporal_workflow_id).signal(signal, args=args)
    except HTTPException:
        raise
    except Exception as exc:
        if _rpc_not_found(exc):
            await _reconcile(session, run)
            raise api_error(409, "action_not_allowed", "The run's workflow no longer exists; the run was closed",
                            run.id) from exc
        raise api_error(503, "temporal_unavailable", f"Could not signal the workflow: {exc}", run.id) from exc


def _run_response(run: ApplicationRun) -> RunResponse:
    jp = run.job_posting
    return RunResponse(
        id=str(run.id),
        job_url=run.job_url,
        status=run.status,
        outcome=derive_outcome(run.status, run.submission_mode, run.email_confirmed_at),
        actions=_actions(run),
        current_state=run.current_state,
        failure_reason=run.failure_reason,
        submission_mode=run.submission_mode,
        origin=run.origin,
        review_round=run.review_round,
        review_deadline=_iso(run.review_deadline),
        retry_of_run_id=str(run.retry_of_run_id) if run.retry_of_run_id else None,
        temporal_workflow_id=run.temporal_workflow_id,
        account_email=run.account_email,
        error_message=run.error_message,
        match_score=run.match_score,
        match_rationale=run.match_rationale,
        tracking_url=run.tracking_url,
        confirmation_html=run.confirmation_html,
        submit_attempted_at=_iso(run.submit_attempted_at),
        email_confirmed_at=_iso(run.email_confirmed_at),
        created_at=run.created_at.isoformat(),
        updated_at=run.updated_at.isoformat(),
        completed_at=_iso(run.completed_at),
        job_posting=JobPostingInfo(
            title=jp.title,
            company=jp.company,
            location=jp.location,
            salary_min=jp.salary_min,
            salary_max=jp.salary_max,
            company_website=jp.company_website,
            industry=jp.industry,
            company_description=jp.company_description,
            work_model=jp.work_model,
            seniority_level=jp.seniority_level,
            experience_required=jp.experience_required,
            posted_date=jp.posted_date,
            technologies=jp.technologies,
            specialties=jp.specialties,
            ats_type=jp.ats_type,
        ) if jp else None,
    )


def _read_rows(artifact: ApplicationArtifact | None) -> list[dict]:
    if artifact is None:
        return []
    try:
        rows = json.loads(Path(artifact.path).read_text())
    except (OSError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> RunResponse:
    return _run_response(await _load(session, run_id))


@router.get("/{run_id}/review", response_model=ReviewResponse)
async def get_review(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    """The evidence of the run's current review round (or its last one, once the review is over)."""
    run = await _load(session, run_id)
    if run.review_round is None:
        raise api_error(409, "not_awaiting_review", "This run has not been in review", run.id)
    artifacts = (await session.execute(
        select(ApplicationArtifact).where(ApplicationArtifact.run_id == run.id)
        .order_by(ApplicationArtifact.created_at)
    )).scalars().all()

    def latest(artifact_type: str, review_round: int | None = None) -> ApplicationArtifact | None:
        found = [a for a in artifacts if a.artifact_type == artifact_type and (
            review_round is None or (a.artifact_metadata or {}).get("review_round") == review_round)]
        return found[-1] if found else None

    answers = _read_rows(latest("submitted_values", run.review_round))
    new_fields = _read_rows(latest("new_fields", run.review_round))
    screenshot = latest("screenshot", run.review_round)
    cover = latest("cover_letter")
    uses_cover_letter = any(row.get("source") == "cover_letter" for row in answers + new_fields)
    jp = run.job_posting
    return ReviewResponse(
        run_id=str(run.id),
        status=run.status,
        submission_mode=run.submission_mode,
        review_round=run.review_round,
        review_deadline=_iso(run.review_deadline),
        job=ReviewJob(title=jp.title if jp else None, company=jp.company if jp else None, url=run.job_url),
        answers=answers,
        new_fields=new_fields,
        screenshot_artifact_id=str(screenshot.id) if screenshot else None,
        cover_letter=CoverLetterInfo(text=(cover.artifact_metadata or {}).get("text"), artifact_id=str(cover.id))
        if cover and uses_cover_letter else None,
    )


@router.get("/{run_id}/steps", response_model=list[StepResponse])
async def get_steps(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[StepResponse]:
    uid = _run_id(run_id)
    run = await session.get(ApplicationRun, uid)
    if run is None:
        raise api_error(404, "run_not_found", "Run not found")

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
        raise api_error(404, "run_not_found", "Run not found")

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


@router.get("/{run_id}/status-updates", response_model=list[StatusUpdateResponse])
async def get_status_updates(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[StatusUpdateResponse]:
    uid = _run_id(run_id)
    run = await session.get(ApplicationRun, uid)
    if run is None:
        raise api_error(404, "run_not_found", "Run not found")

    result = await session.execute(
        select(ApplicationStatusUpdate)
        .where(ApplicationStatusUpdate.run_id == uid)
        .order_by(ApplicationStatusUpdate.email_date)
    )
    updates = result.scalars().all()

    return [
        StatusUpdateResponse(
            id=str(u.id),
            status_type=u.status_type,
            subject=u.subject,
            sender=u.sender,
            summary=u.summary,
            email_date=u.email_date.isoformat(),
            confidence=u.confidence,
            created_at=u.created_at.isoformat(),
        )
        for u in updates
    ]


# ── Actions ────────────────────────────────────────────────────────────────

@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_runs(
    body: BulkDeleteRequest,
    session: AsyncSession = Depends(get_session),
) -> BulkDeleteResponse:
    if body.run_ids:
        uids = [_run_id(rid) for rid in body.run_ids]
        runs = (await session.execute(
            select(ApplicationRun).where(ApplicationRun.id.in_(uids))
        )).scalars().all()
    elif body.status:
        runs = (await session.execute(
            select(ApplicationRun).where(ApplicationRun.status == body.status.upper())
        )).scalars().all()
    else:
        raise api_error(400, "missing_selection", "Provide run_ids or status")

    deletable = [run for run in runs if "delete" in _actions(run)]
    for run in deletable:
        await session.delete(run)  # steps, artifacts and status updates go with it; claims stay
    await session.commit()
    return BulkDeleteResponse(deleted=len(deletable), skipped=len(runs) - len(deletable), success=True)


@router.post("/{run_id}/approve", response_model=ActionResponse, status_code=202)
async def approve_run(
    run_id: str,
    body: ReviewDecision,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    """Approve exactly what review round `review_round` showed; the workflow re-fills it and submits."""
    run = await _load(session, run_id)
    _require(run, "approve", "not_awaiting_review")
    if body.review_round != run.review_round:
        raise api_error(409, "stale_review_round",
                        f"Review round {body.review_round} is not the current round ({run.review_round})", run.id)
    await _signal(request, session, run, "approve_submission", [body.review_round])
    return ActionResponse(success=True, message="Approval sent", run_id=str(run.id))


@router.post("/{run_id}/reject", response_model=ActionResponse, status_code=202)
async def reject_run(
    run_id: str,
    body: ReviewRejection,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    run = await _load(session, run_id)
    _require(run, "reject", "not_awaiting_review")
    if body.review_round != run.review_round:
        raise api_error(409, "stale_review_round",
                        f"Review round {body.review_round} is not the current round ({run.review_round})", run.id)
    await _signal(request, session, run, "reject_submission", [body.review_round, body.reason])
    return ActionResponse(success=True, message="Rejection sent", run_id=str(run.id))


@router.post("/{run_id}/cancel", response_model=ActionResponse, status_code=202)
async def cancel_run(
    run_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    """Ask the workflow to cancel; it closes the browser and writes CANCELLED (or SUBMIT_UNCERTAIN)."""
    run = await _load(session, run_id)
    _require(run, "cancel")
    try:
        await _temporal(request).get_workflow_handle(run.temporal_workflow_id).cancel()
    except HTTPException:
        raise
    except Exception as exc:
        if not _rpc_not_found(exc):
            raise api_error(503, "temporal_unavailable", f"Could not cancel the workflow: {exc}", run.id) from exc
        await _reconcile(session, run)
        return ActionResponse(success=True, message="Run closed (its workflow no longer exists)", run_id=str(run.id))
    return ActionResponse(success=True, message="Cancellation requested", run_id=str(run.id))


@router.post("/{run_id}/retry", response_model=ActionResponse)
async def retry_run(
    run_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    """A new run for the same job, linked to this one, in the current submission mode (contract §7).
    The original run, its alias and its evidence stay as they are."""
    run = await _load(session, run_id)
    _require(run, "retry")
    temporal = _temporal(request)
    if run.temporal_workflow_id:  # e.g. a row closed by reconciliation whose workflow lives on
        try:
            await temporal.get_workflow_handle(run.temporal_workflow_id).terminate(
                reason=f"superseded by a retry of run {run.id}")
        except Exception as exc:
            if not _rpc_not_found(exc):  # not found = already closed
                raise api_error(503, "temporal_unavailable",
                                f"Could not stop the original workflow: {exc}", run.id) from exc
    try:
        new_id = await submit_job_for_application(
            run.job_posting.url if run.job_posting else run.job_url, temporal,
            origin=run.origin or "manual", retry_of_run_id=run.id)
    except (AlreadyApplied, RunInFlight) as exc:
        raise enqueue_refused(exc) from exc
    except Exception as exc:
        raise api_error(503, "temporal_unavailable", f"Failed to start workflow: {exc}", run.id) from exc
    return ActionResponse(success=True, message="Retry started", run_id=new_id)


@router.post("/{run_id}/confirm-submitted", response_model=ActionResponse)
async def confirm_submitted(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    """The user checked: the uncertain run's application did go through (contract §6)."""
    run = await _load(session, run_id)
    _require(run, "confirm_submitted")
    result = await session.execute(
        update(ApplicationRun)
        .where(ApplicationRun.id == run.id, ApplicationRun.status == "SUBMIT_UNCERTAIN")
        .values(status="COMPLETED", current_state="DONE", failure_reason=None,
                error_message=f"{run.error_message or ''} (confirmed submitted by the user)".strip(),
                completed_at=func.coalesce(ApplicationRun.completed_at, func.now()), updated_at=func.now())
        .execution_options(synchronize_session=False))
    await session.commit()
    if result.rowcount != 1:
        raise api_error(409, "action_not_allowed", "The run is no longer uncertain", run.id)
    return ActionResponse(success=True, message="Marked as submitted", run_id=str(run.id))


@router.post("/{run_id}/confirm-not-submitted", response_model=ActionResponse)
async def confirm_not_submitted(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    """The user checked: nothing was submitted. The run becomes FAILED (keeping its reason) and the
    job's submission claim is released: the only way to apply to it again (contract §6)."""
    run = await _load(session, run_id)
    _require(run, "confirm_not_submitted")
    attempted = _iso(run.submit_attempted_at)
    result = await session.execute(
        update(ApplicationRun)
        .where(ApplicationRun.id == run.id, ApplicationRun.status == "SUBMIT_UNCERTAIN")
        .values(status="FAILED", submit_attempted_at=None, updated_at=func.now(),
                error_message=f"{run.error_message or ''} (confirmed not submitted by the user; "
                              f"submit was attempted at {attempted})".strip())
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await session.rollback()
        raise api_error(409, "action_not_allowed", "The run is no longer uncertain", run.id)
    await session.execute(delete(SubmissionClaim).where(
        SubmissionClaim.posting_key == posting_key(run.job_posting_id), SubmissionClaim.run_id == run.id))
    await session.commit()
    return ActionResponse(success=True, message="Marked as not submitted; the job can be applied to again",
                          run_id=str(run.id))


@router.delete("/{run_id}", response_model=ActionResponse)
async def delete_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    run = await _load(session, run_id)
    _require(run, "delete")
    await session.delete(run)  # steps, artifacts and status updates go with it; its claim stays
    await session.commit()
    return ActionResponse(success=True, message="Run deleted")


# ── Artifact content types ────────────────────────────────────────────────

_MEDIA_TYPES: dict[str, str] = {
    "screenshot": "image/png",
    "cover_letter": "application/pdf",
    "trace": "application/zip",
    "form_schema": "application/json",
    "answer_plan": "application/json",
    "field_mappings": "application/json",  # legacy: runs from before the answer engine
    "submitted_values": "application/json",
    "new_fields": "application/json",
    "submit_evidence": "text/plain; charset=utf-8",  # JSON lines
    "confirmation": "text/plain; charset=utf-8",
}
_HTML_TYPES = {"confirmation_html"}


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
        raise api_error(422, "invalid_artifact_id", "Invalid artifact_id format")

    result = await session.execute(
        select(ApplicationArtifact).where(
            ApplicationArtifact.id == aid,
            ApplicationArtifact.run_id == uid,
        )
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise api_error(404, "artifact_not_found", "Artifact not found")

    file_path = Path(artifact.path)
    if not file_path.is_absolute():
        file_path = file_path.resolve()

    if not file_path.is_file():
        raise api_error(404, "artifact_not_found", "Artifact file not found on disk")

    headers = {"X-Content-Type-Options": "nosniff"}
    if artifact.artifact_type in _HTML_TYPES or file_path.suffix.lower() in (".html", ".htm"):
        # Job-site markup: never rendered in the dashboard's origin (contract §8 / A6).
        media_type = "text/plain; charset=utf-8"
        headers["Content-Security-Policy"] = "sandbox"
    else:
        media_type = _MEDIA_TYPES.get(artifact.artifact_type, "application/octet-stream")
    return FileResponse(file_path, media_type=media_type, filename=file_path.name, headers=headers)
