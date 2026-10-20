"""Probe ApplicationWorkflow (real workflow code) against mocked activities on an ephemeral dev server.

Probe A: run-slot gate as a long-polling activity vs worker activity slots (starvation).
Probe B: worker restart mid-activity -> fail_incomplete_runs-equivalent marks FAILED -> what Temporal does.
The mocked DB is an in-memory dict that reproduces the status writes of db_ops.py / worker.py.
"""
import asyncio
import sys
import uuid
from datetime import timedelta

from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from yahbah.schemas import (
    CoverLetterInput, CoverLetterOutput, DiscoverInput, DiscoverOutput, DuplicateCheckInput,
    DuplicateCheckOutput, FillInput, FillOutput, JobMetadataInput, JobMetadataOutput, MapAnswersInput,
    MapAnswersOutput, MapFormInput, MapFormOutput, SubmitInput, SubmitOutput,
)
from yahbah.workflows.application import ApplicationWorkflow, ApplicationWorkflowInput

DB: dict[str, dict] = {}          # run_id -> {"status", "state"}
EVENTS: list[str] = []
FILL_DELAY = {"secs": 0.2}
MAX_CONCURRENT_RUNS = 1


def log(msg: str) -> None:
    EVENTS.append(msg)
    print(msg, flush=True)


@activity.defn(name="acquire_run_slot_activity")
async def acquire(run_id: str) -> None:
    while True:
        running = sum(1 for k, r in DB.items() if r["status"] == "RUNNING" and k != run_id)
        if running < MAX_CONCURRENT_RUNS:
            DB[run_id].update(status="RUNNING", state="STARTING")
            return
        DB[run_id]["state"] = "QUEUED"
        activity.heartbeat("queued")
        await asyncio.sleep(0.2)


@activity.defn(name="update_run_state_activity")
async def update_state(run_id: str, state: str) -> None:
    DB[run_id].update(status="RUNNING", state=state)  # same as db_ops: always status=RUNNING


@activity.defn(name="mark_run_completed_activity")
async def completed(run_id: str) -> None:
    DB[run_id].update(status="COMPLETED", state="DONE")


@activity.defn(name="mark_run_failed_activity")
async def failed(run_id: str, msg: str) -> None:
    DB[run_id].update(status="FAILED")


@activity.defn(name="mark_run_duplicate_activity")
async def duplicate(run_id: str, msg: str) -> None:
    DB[run_id].update(status="DUPLICATE")


@activity.defn(name="check_duplicate_activity")
async def check_dup(i: DuplicateCheckInput) -> DuplicateCheckOutput:
    return DuplicateCheckOutput(is_duplicate=False)


@activity.defn(name="close_session_activity")
async def close_session(run_id: str) -> None:
    pass


@activity.defn(name="discover_activity")
async def discover(i: DiscoverInput) -> DiscoverOutput:
    return DiscoverOutput(page_url=i.job_url, board="generic", account_email="x", job_description="d")


@activity.defn(name="map_form_activity")
async def map_form(i: MapFormInput) -> MapFormOutput:
    return MapFormOutput(form_schema_dict={"fields": [], "page_url": i.job_url, "board": "generic"})


@activity.defn(name="extract_job_metadata_activity")
async def metadata(i: JobMetadataInput) -> JobMetadataOutput:
    return JobMetadataOutput()


@activity.defn(name="map_answers_activity")
async def map_answers(i: MapAnswersInput) -> MapAnswersOutput:
    return MapAnswersOutput(field_mappings=[])


@activity.defn(name="generate_cover_letter_activity")
async def cover(i: CoverLetterInput) -> CoverLetterOutput:
    return CoverLetterOutput(cover_letter_path="", cover_letter_text="")


@activity.defn(name="fill_activity")
async def fill(i: FillInput) -> FillOutput:
    info = activity.info()
    log(f"fill start run={i.run_id[:8]} attempt={info.attempt}")
    try:
        await asyncio.sleep(FILL_DELAY["secs"])  # no heartbeats, like the real browser activities between beats
    except asyncio.CancelledError:
        log(f"fill cancelled run={i.run_id[:8]} attempt={info.attempt}")
        raise
    log(f"fill done run={i.run_id[:8]} attempt={info.attempt}")
    return FillOutput(testing_mode=False, form_schema_dict={}, field_mappings=[], written={})


@activity.defn(name="submit_activity")
async def submit(i: SubmitInput) -> SubmitOutput:
    log(f"SUBMIT CLICKED run={i.run_id[:8]} attempt={activity.info().attempt} db_status={DB[i.run_id]['status']}")
    return SubmitOutput(confirmation_url="u", confirmation_text="t")


ACTS = [acquire, update_state, completed, failed, duplicate, check_dup, close_session,
        discover, map_form, metadata, map_answers, cover, fill, submit]


async def start_runs(client, n: int, tq: str):
    handles = []
    for _ in range(n):
        rid = str(uuid.uuid4())
        DB[rid] = {"status": "PENDING", "state": None}
        handles.append(await client.start_workflow(
            ApplicationWorkflow.run, ApplicationWorkflowInput(run_id=rid, job_url="https://example.test/job"),
            id=f"application-{rid}", task_queue=tq))
    return handles


async def statuses(handles):
    return [(await h.describe()).status.name for h in handles]


async def probe_a(env):
    print("\n=== Probe A: queued runs vs worker activity slots ===")
    for slots, runs in ((10, 6), (4, 6)):
        DB.clear()
        tq = f"probe-a-{slots}"
        async with Worker(env.client, task_queue=tq, workflows=[ApplicationWorkflow], activities=ACTS,
                          max_concurrent_activities=slots):
            handles = await start_runs(env.client, runs, tq)
            await asyncio.sleep(12)
            st = await statuses(handles)
            db = sorted((r["status"], str(r["state"])) for r in DB.values())
            print(f"slots={slots} runs={runs}: temporal={st}")
            print(f"   db={db}")
            for h in handles:
                try:
                    await h.terminate("probe cleanup")
                except Exception:
                    pass


async def probe_b(env):
    print("\n=== Probe B: graceful worker restart during a browser activity ===")
    DB.clear()
    tq = "probe-b"
    FILL_DELAY["secs"] = 3
    w1 = Worker(env.client, task_queue=tq, workflows=[ApplicationWorkflow], activities=ACTS)
    w1_task = asyncio.create_task(w1.run())
    handles = await start_runs(env.client, 3, tq)   # one run gets the slot, the others queue
    for _ in range(100):
        if any(e.startswith("fill start") for e in EVENTS):
            break
        await asyncio.sleep(0.1)
    in_fill = [e.split("run=")[1].split()[0] for e in EVENTS if e.startswith("fill start")][0]
    rid_a = next(k for k in DB if k.startswith(in_fill))
    rid_b = next(k for k in DB if k != rid_a)
    a = next(h for h in handles if h.id.endswith(rid_a)); b = next(h for h in handles if h.id.endswith(rid_b))
    print(f"before restart: {DB}")
    await w1.shutdown()                       # what ctrl-C / restart does to worker.run()
    await w1_task
    for rid, r in DB.items():                 # worker.fail_incomplete_runs equivalent
        if r["status"] == "RUNNING":
            r["status"] = "FAILED"
    print(f"after fail_incomplete_runs: {DB}")
    FILL_DELAY["secs"] = 3
    async with Worker(env.client, task_queue=tq, workflows=[ApplicationWorkflow], activities=ACTS):
        peak = 0
        for _ in range(150):
            peak = max(peak, sum(1 for r in DB.values() if r["status"] == "RUNNING"))
            await asyncio.sleep(0.1)
        print(f"after restart: {DB} peak_concurrent_RUNNING={peak} "
              f"(max_concurrent_runs={MAX_CONCURRENT_RUNS})")
        print(f"temporal: A={(await a.describe()).status.name} B={(await b.describe()).status.name}")
        for h in handles:
            try:
                await h.terminate("probe cleanup")
            except Exception:
                pass


async def main():
    which = sys.argv[1:] or ["a", "b"]
    async with await WorkflowEnvironment.start_local(dev_server_existing_path="/opt/homebrew/bin/temporal") as env:
        if "a" in which:
            await probe_a(env)
        if "b" in which:
            await probe_b(env)


asyncio.run(main())
