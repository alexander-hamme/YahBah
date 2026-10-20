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


# ── DB rules: implemented by batch-1 task 3 ─────────────────────────────────────

@dataclass(frozen=True)
class Blocker:
    run_id: UUID | None  # None when only a submission claim remains (its run was deleted)
    status: str | None
    reason: Literal["in_flight", "completed", "submit_uncertain", "attempted", "email_confirmed", "claim"]


async def possibly_applied(session, job_posting_id: UUID, *, exclude_run_id: UUID | None = None) -> Blocker | None:
    """What blocks a new application to this posting, or None (contract §5)."""
    raise NotImplementedError("possibly_applied: batch-1 task 3")


async def possibly_applied_url(session, url: str, *, exclude_run_id: UUID | None = None) -> Blocker | None:
    """possibly_applied() for a URL: resolves the posting by its normalized URL (contract §5)."""
    raise NotImplementedError("possibly_applied_url: batch-1 task 3")


async def claim_submission(run_id: str, workflow_id: str, job_posting_id: UUID) -> bool:
    """Atomically record the submit attempt and claim the posting; click only if True (contract §6)."""
    raise NotImplementedError("claim_submission: batch-1 task 3")
