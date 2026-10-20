"""
The attempt lease, taken when every activity attempt starts (contract §7).

A run's lease is the (workflow, activity sequence, attempt) acting on it now (application_run.lease_*).
RunLeaseInterceptor, registered on the worker, takes it before any activity of a run executes, so as
soon as a retry (or the next stage) starts, an older attempt that is still running, for example one
that overran its timeout and swallowed the cancellation, can no longer claim the submission or write
the run (db_ops.fenced_update, run_state.claim_submission). Taking it is monotonic: an older attempt
can never take it back. An attempt that finds a newer lease fails as `superseded` before it runs.
"""
import uuid

from temporalio import activity
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from yahbah.workflows.activities.db_ops import take_lease

# Local cleanup, not a run write: the browser's own owner check (SessionRegistry) governs it.
UNLEASED = frozenset({"close_session_activity"})


def _run_id(args) -> str | None:
    """The run an activity acts on: its first argument, or that argument's run_id."""
    first = args[0] if args else None
    candidate = first if isinstance(first, str) else (
        first.get("run_id") if isinstance(first, dict) else getattr(first, "run_id", None))
    try:
        return str(uuid.UUID(str(candidate))) if candidate else None
    except ValueError:
        return None


class _TakeLease(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput):
        if activity.info().activity_type not in UNLEASED and (run_id := _run_id(input.args)):
            await take_lease(run_id)
        return await super().execute_activity(input)


class RunLeaseInterceptor(Interceptor):
    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _TakeLease(next)
