"""
Run lifecycle vocabulary and the pure rules over it (docs/wiki/contracts/run-lifecycle.md).

One source for run statuses, failure reasons, submission modes, origins, outcomes and the actions a
run allows. The API derives `outcome` and `actions` here; the frontend renders them and never
re-derives eligibility from `status`. possibly_applied() is the one dedup predicate and
claim_submission() the one way to record a submit attempt.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from temporalio import activity

from yahbah.db.models import ApplicationRun, JobPosting, SubmissionClaim
from yahbah.db.session import AsyncSessionLocal
from yahbah.url_utils import canonicalize_url

RunStatus = Literal[
    "PENDING", "RUNNING", "AWAITING_REVIEW",                                    # in flight
    "COMPLETED", "SUBMIT_UNCERTAIN", "FAILED", "CANCELLED", "EXPIRED", "DUPLICATE",  # terminal
]
IN_FLIGHT: frozenset[str] = frozenset({"PENDING", "RUNNING", "AWAITING_REVIEW"})
TERMINAL: frozenset[str] = frozenset(get_args(RunStatus)) - IN_FLIGHT

FailureReason = Literal[
    # discover / map
    "form_not_found", "form_unmapped", "form_changed", "job_closed", "login_wall", "captcha", "blocked",
    # answers / fill
    "needs_answer", "fill_failed", "possibly_submitted",
    # submit
    "submit_button_not_found", "claim_denied", "email_code_entry", "email_code_timeout", "submit_unverified",
    # infrastructure
    "browser_error", "llm_error", "timeout", "superseded", "internal_error",
    # user / policy
    "cancelled", "review_rejected", "review_expired", "duplicate",
]

SubmissionMode = Literal["testing", "review"]  # "auto_reviewed" arrives in batch 2 with the reviewer pass
DEFAULT_SUBMISSION_MODE: SubmissionMode = "review"  # missing or invalid setting: fail closed

Origin = Literal["manual", "discovered"]

Outcome = Literal[
    "queued", "in_progress", "awaiting_review", "test_only", "email_verified", "submitted",
    "submit_uncertain", "failed", "cancelled", "expired", "duplicate",
]

Action = Literal["cancel", "retry", "approve", "reject", "confirm_submitted", "confirm_not_submitted", "delete"]

_RETRYABLE: frozenset[str] = frozenset({"FAILED", "CANCELLED", "EXPIRED", "DUPLICATE"})
_OUTCOME_BY_STATUS: dict[str, Outcome] = {
    "PENDING": "queued", "RUNNING": "in_progress", "AWAITING_REVIEW": "awaiting_review",
    "SUBMIT_UNCERTAIN": "submit_uncertain", "FAILED": "failed", "CANCELLED": "cancelled",
    "EXPIRED": "expired", "DUPLICATE": "duplicate",
}


def derive_outcome(status: str, submission_mode: str | None, email_confirmed_at: datetime | None) -> Outcome:
    """What the run means to the user. submission_mode None = a run from before the contract (legacy)."""
    if status == "COMPLETED":
        if submission_mode == "testing":
            return "test_only"
        return "email_verified" if email_confirmed_at else "submitted"
    return _OUTCOME_BY_STATUS[status]


def allowed_actions(status: str, submission_mode: str | None, submit_attempted_at: datetime | None) -> list[Action]:
    """The user actions a run allows; the routes enforce the same rules (409 otherwise)."""
    if status in IN_FLIGHT:
        return ["approve", "reject", "cancel"] if status == "AWAITING_REVIEW" else ["cancel"]
    if status == "SUBMIT_UNCERTAIN":  # resolve first: deleting it would hide a possible submission
        return ["confirm_submitted", "confirm_not_submitted"]
    retryable = (status in _RETRYABLE and submit_attempted_at is None) or (
        status == "COMPLETED" and submission_mode == "testing")
    return ["retry", "delete"] if retryable else ["delete"]


# ── DB rules ────────────────────────────────────────────────────────────────────

BlockReason = Literal["in_flight", "completed", "submit_uncertain", "attempted", "email_confirmed", "claim"]
_BLOCK_ORDER: tuple[BlockReason, ...] = ("in_flight", "completed", "submit_uncertain", "attempted", "email_confirmed")


@dataclass(frozen=True)
class Blocker:
    run_id: UUID | None  # None when only a submission claim remains (its run was deleted)
    status: str | None
    reason: BlockReason


def posting_key(job_posting_id: UUID) -> str:
    """The submission_claim key of a posting (batch 1; B2's (ats, tenant, job_id) replaces it)."""
    return str(job_posting_id)


def _block_reason(status: str, submit_attempted_at, email_confirmed_at) -> BlockReason | None:
    if status in IN_FLIGHT:
        return "in_flight"
    if status == "COMPLETED":
        return "completed"
    if status == "SUBMIT_UNCERTAIN":
        return "submit_uncertain"
    if submit_attempted_at is not None:
        return "attempted"
    if email_confirmed_at is not None:
        return "email_confirmed"
    return None


async def possibly_applied(session, job_posting_id: UUID, *, exclude_run_id: UUID | None = None) -> Blocker | None:
    """What blocks a new application to this posting, or None (contract §5).

    A run blocks unless it is a testing-mode run or ended (FAILED, CANCELLED, EXPIRED, DUPLICATE)
    without ever claiming submit or getting a confirmation email. The posting's submission claim
    blocks too, unless it is exclude_run_id's own.
    """
    query = select(ApplicationRun.id, ApplicationRun.status, ApplicationRun.submit_attempted_at,
                   ApplicationRun.email_confirmed_at).where(
        ApplicationRun.job_posting_id == job_posting_id,
        ApplicationRun.submission_mode.is_distinct_from("testing"),
        or_(ApplicationRun.status.in_(IN_FLIGHT | {"COMPLETED", "SUBMIT_UNCERTAIN"}),
            ApplicationRun.submit_attempted_at.is_not(None),
            ApplicationRun.email_confirmed_at.is_not(None)),
    ).order_by(ApplicationRun.created_at)
    if exclude_run_id is not None:
        query = query.where(ApplicationRun.id != exclude_run_id)
    found = [Blocker(row.id, row.status, reason) for row in (await session.execute(query)).all()
             if (reason := _block_reason(row.status, row.submit_attempted_at, row.email_confirmed_at))]
    if found:
        return min(found, key=lambda b: _BLOCK_ORDER.index(b.reason))

    claim = (await session.execute(
        select(SubmissionClaim).where(SubmissionClaim.posting_key == posting_key(job_posting_id))
    )).scalar_one_or_none()
    if claim is None or (exclude_run_id is not None and claim.run_id == exclude_run_id):
        return None
    status = None
    if claim.run_id is not None:
        status = (await session.execute(
            select(ApplicationRun.status).where(ApplicationRun.id == claim.run_id))).scalar_one_or_none()
    return Blocker(claim.run_id, status, "claim")


async def possibly_applied_url(session, url: str, *, exclude_run_id: UUID | None = None) -> Blocker | None:
    """possibly_applied() for a URL: every posting whose URL or canonical URL is `url`, or `url` in its
    canonical form (contract §5)."""
    urls = {url, canonicalize_url(url)}
    posting_ids = (await session.execute(
        select(JobPosting.id).where(or_(JobPosting.url.in_(urls), JobPosting.canonical_url.in_(urls)))
    )).scalars().all()
    for posting_id in posting_ids:
        if blocker := await possibly_applied(session, posting_id, exclude_run_id=exclude_run_id):
            return blocker
    return None


# ── the attempt lease (contract §7) ──────────────────────────────────────────────

Owner = tuple[str, int, int]  # (workflow_id, activity sequence number, attempt)


def activity_owner() -> Owner | None:
    """The calling activity attempt, or None outside an activity (scripts, direct calls in tests).
    The workflow's activity ids are its command sequence numbers ("1", "2", ...): later = higher."""
    if not activity.in_activity():
        return None
    info = activity.info()
    return (info.workflow_id, int(info.activity_id) if info.activity_id.isdigit() else 0, info.attempt)


def attempt_tag() -> str:
    """"<activity seq>-<attempt>" of the calling activity attempt ("" outside one): makes the evidence
    files of different attempts distinct, so an attempt that lost the lease can't overwrite its retry's."""
    owner = activity_owner()
    return f"{owner[1]}-{owner[2]}" if owner else ""


def lease_clauses(owner: Owner | None) -> list:
    """WHERE clauses for "this attempt may hold the run's lease": no newer attempt of the same workflow
    has taken it. Outside an activity there is no lease check."""
    if owner is None:
        return []
    workflow_id, seq, attempt = owner
    return [or_(ApplicationRun.lease_workflow_id.is_distinct_from(workflow_id),
                ApplicationRun.lease_activity_seq.is_(None),
                tuple_(ApplicationRun.lease_activity_seq, ApplicationRun.lease_attempt) <= tuple_(seq, attempt))]


def lease_values(owner: Owner | None) -> dict:
    """SET values that (re)take the lease for `owner`, together with lease_clauses()."""
    if owner is None:
        return {}
    return {"lease_workflow_id": owner[0], "lease_activity_seq": owner[1], "lease_attempt": owner[2]}


def _owned(run_id: str, workflow_id: str, owner: Owner | None) -> list:
    return [ApplicationRun.id == UUID(run_id), ApplicationRun.temporal_workflow_id == workflow_id,
            ApplicationRun.status.not_in(TERMINAL), *lease_clauses(owner)]


async def claim_submission(run_id: str, workflow_id: str, job_posting_id: UUID) -> bool:
    """Atomically record the submit attempt and claim the posting; click only if True (contract §6).

    One transaction: the run must be RUNNING, owned by workflow_id, leased to the calling activity
    attempt and never attempted, and the posting must be unclaimed. Anything else rolls back and
    returns False.
    """
    owner = activity_owner()
    async with AsyncSessionLocal() as session:
        marked = await session.execute(
            update(ApplicationRun).where(*_owned(run_id, workflow_id, owner), ApplicationRun.status == "RUNNING",
                                         ApplicationRun.submit_attempted_at.is_(None))
            .values(submit_attempted_at=func.now(), updated_at=func.now(), **lease_values(owner))
            .execution_options(synchronize_session=False))
        if marked.rowcount != 1:
            await session.rollback()
            return False
        claimed = await session.execute(_insert_claim(run_id, job_posting_id).returning(SubmissionClaim.posting_key))
        if claimed.first() is None:  # the posting is already claimed
            await session.rollback()
            return False
        await session.commit()
        return True


async def record_possible_submission(run_id: str, workflow_id: str, job_posting_id: UUID) -> None:
    """Agent tripwire fired: mark the run attempted and claim the posting, so it ends SUBMIT_UNCERTAIN (contract §6).

    Unlike claim_submission this never refuses: a submission may already have happened. The run is
    marked attempted only while the caller holds its lease, but the posting is always claimed
    (unless already claimed), whoever records it.
    """
    owner = activity_owner()
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ApplicationRun)
            .where(*_owned(run_id, workflow_id, owner), ApplicationRun.submit_attempted_at.is_(None))
            .values(submit_attempted_at=func.now(), updated_at=func.now())
            .execution_options(synchronize_session=False))
        await session.execute(_insert_claim(run_id, job_posting_id))
        await session.commit()


def _insert_claim(run_id: str, job_posting_id: UUID):
    return (pg_insert(SubmissionClaim)
            .values(posting_key=posting_key(job_posting_id), run_id=UUID(run_id), claimed_at=func.now())
            .on_conflict_do_nothing(index_elements=["posting_key"]))
