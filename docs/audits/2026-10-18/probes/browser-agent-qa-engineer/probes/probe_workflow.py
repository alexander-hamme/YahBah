"""Feasibility + fault-injection probe: the real ApplicationWorkflow against mocked activities on an
ephemeral local Temporal dev server (existing `temporal` binary, no download, no DB, no browser)."""
import asyncio, time, uuid
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from yahbah.schemas import *  # noqa
from yahbah.workflows.application import ApplicationWorkflow, ApplicationWorkflowInput

calls: list[str] = []
faults: dict[str, list] = {}

def mk(name, ret=None):
    @activity.defn(name=name)
    async def _a(*args):
        attempt = activity.info().attempt
        calls.append(f"{name}#{attempt}")
        plan = faults.get(name) or []
        if attempt <= len(plan) and plan[attempt - 1] is not None:
            raise plan[attempt - 1]
        return ret() if callable(ret) else ret
    return _a

SCHEMA = {"fields": [], "page_url": "https://x", "board": "generic"}
ACTS = [
    mk("acquire_run_slot_activity"), mk("update_run_state_activity"),
    mk("discover_activity", lambda: DiscoverOutput(page_url="https://x", board="generic", account_email="a@x", job_description="jd")),
    mk("map_form_activity", lambda: MapFormOutput(form_schema_dict=SCHEMA)),
    mk("extract_job_metadata_activity", lambda: JobMetadataOutput()),
    mk("check_duplicate_activity", lambda: DuplicateCheckOutput(is_duplicate=False)),
    mk("map_answers_activity", lambda: MapAnswersOutput(field_mappings=[])),
    mk("generate_cover_letter_activity", lambda: CoverLetterOutput(cover_letter_path="/tmp/c.pdf", cover_letter_text="c")),
    mk("fill_activity", lambda: FillOutput(testing_mode=False, form_schema_dict=SCHEMA, field_mappings=[], written={})),
    mk("submit_activity", lambda: SubmitOutput(confirmation_url="https://x/thanks", confirmation_text="ok")),
    mk("mark_run_completed_activity"), mk("mark_run_failed_activity"), mk("mark_run_duplicate_activity"),
    mk("close_session_activity"),
]

async def run_case(env, name, plan):
    calls.clear(); faults.clear(); faults.update(plan)
    t = time.time(); outcome = "completed"
    async with Worker(env.client, task_queue="probe", workflows=[ApplicationWorkflow], activities=ACTS,
                      workflow_runner=UnsandboxedWorkflowRunner()):
        try:
            await env.client.execute_workflow(ApplicationWorkflow.run, ApplicationWorkflowInput(run_id=str(uuid.uuid4()), job_url="https://x"),
                                              id=f"probe-{uuid.uuid4().hex[:6]}", task_queue="probe")
        except WorkflowFailureError as e:
            outcome = f"failed: {type(e.cause).__name__}"
    tail = [c for c in calls if not c.startswith("update_run_state")]
    print(f"{name:44} {outcome:28} {time.time()-t:4.1f}s  {tail[-5:]}")

async def main():
    t = time.time()
    async with await WorkflowEnvironment.start_local(dev_server_existing_path="/opt/homebrew/bin/temporal") as env:
        print(f"dev server up in {time.time()-t:.1f}s")
        await run_case(env, "happy path", {})
        await run_case(env, "submit crashes after click (retryable error)", {"submit_activity": [RuntimeError("CDP connection closed")]})
        await run_case(env, "submit non-retryable (submit_unverified)", {"submit_activity": [ApplicationError("submit_unverified", type="submit_unverified", non_retryable=True)]})
        await run_case(env, "fill fails twice (retryable)", {"fill_activity": [RuntimeError("x"), RuntimeError("y")]})
    print(f"total {time.time()-t:.1f}s")
asyncio.run(main())
