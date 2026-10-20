"""Error bodies of the API routes (contract §8): {"detail": {"code", "message", "run_id"?}}."""
from fastapi import HTTPException

from yahbah.jobs import AlreadyApplied, RunInFlight


def api_error(status_code: int, code: str, message: str, run_id=None) -> HTTPException:
    detail = {"code": code, "message": message}
    if run_id is not None:
        detail["run_id"] = str(run_id)
    return HTTPException(status_code=status_code, detail=detail)


def enqueue_refused(exc: AlreadyApplied | RunInFlight) -> HTTPException:
    """409 for a posting that can't take a new run (yahbah.jobs.submit_job_for_application)."""
    if isinstance(exc, AlreadyApplied):
        return api_error(409, "already_applied", f"This job was already applied to, or may have been "
                                                 f"({exc.blocker.reason}).", exc.blocker.run_id)
    return api_error(409, "run_in_flight", "This job already has a run in progress.", exc.run_id)
