"""Temporal for tests (marker `temporal`): a local dev server, stub activities registered under the
real activity names, and the real worker entrypoint (yahbah.worker.main) running in-process.

- Server: WorkflowEnvironment.start_local with the `temporal` binary on PATH. Without it, a
  time-skipping test server is used only if YAHBAH_TEMPORAL_TEST_SERVER names an existing binary;
  otherwise the tests skip (the SDK would download one, and tests stay offline).
- Stubs: every activity in yahbah.workflows.activities.browser and .llm is replaced by a stub of
  the same name that records its call (attempt, workflow id, timeouts) and returns a benign default
  built from the real activity's return type. db_ops activities stay real, against the test DB.
  `stubs.on(name, handler)` overrides one; `stubs.gate(name)` makes it block until released.
- Worker: WorkerHarness runs the real `yahbah.worker.main()` (its start/stop status writes, its
  Worker options), with the Temporal client, task queue and Gmail pollers swapped for test ones and
  stubs swapped in by activity name. stop() is a graceful shutdown (task cancel, like Ctrl-C);
  restart() is stop() + start(), i.e. a worker restart mid-activity.
"""
import asyncio
import dataclasses
import importlib
import inspect
import os
import shutil
import typing
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from harness.contract import call_with_supported, make
from harness.forms import APPLICANT_EMAIL

STUBBED_MODULES = ("yahbah.workflows.activities.browser", "yahbah.workflows.activities.llm")
ACTIVITY_MODULES = STUBBED_MODULES + ("yahbah.workflows.activities.db_ops",)


async def start_env() -> WorkflowEnvironment:
    if path := shutil.which("temporal"):
        return await WorkflowEnvironment.start_local(dev_server_existing_path=path, dev_server_log_level="error")
    if (path := os.environ.get("YAHBAH_TEMPORAL_TEST_SERVER")) and os.path.exists(path):
        return await WorkflowEnvironment.start_time_skipping(test_server_existing_path=path)
    pytest.skip("no `temporal` binary on PATH (and no YAHBAH_TEMPORAL_TEST_SERVER); tests never download one")


def activity_name(fn: Callable) -> str | None:
    defn = getattr(fn, "__temporal_activity_definition", None)
    return defn.name if defn else None


def activity_definitions(modules=ACTIVITY_MODULES) -> dict[str, Callable]:
    out = {}
    for module_name in modules:
        module = importlib.import_module(module_name)
        for _, fn in inspect.getmembers(module, inspect.iscoroutinefunction):
            if (name := activity_name(fn)) and fn.__module__ == module_name:
                out[name] = fn
    return out


@dataclass
class ActivityCall:
    name: str
    args: tuple
    attempt: int
    workflow_id: str
    heartbeat_timeout: timedelta | None
    start_to_close_timeout: timedelta | None
    completed: bool = False
    cancelled: bool = False

    @property
    def input(self) -> dict:
        """The first argument (activity inputs are dataclasses, decoded here as dicts)."""
        return self.args[0] if self.args and isinstance(self.args[0], dict) else {}

    @property
    def run_id(self) -> str | None:
        first = self.args[0] if self.args else None
        return first.get("run_id") if isinstance(first, dict) else first


class Gate:
    """Makes a stub block: `entered` is set when a call arrives, the call waits for `release`
    (at most `timeout` seconds, so a forgotten gate can't hang the suite)."""

    def __init__(self, attempts: set[int] | None = None, timeout: float = 20.0) -> None:
        self.attempts = attempts
        self.timeout = timeout
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[ActivityCall] = []

    def applies(self, call: ActivityCall) -> bool:
        return self.attempts is None or call.attempt in self.attempts

    async def wait_entered(self, timeout: float = 15.0) -> ActivityCall:
        await asyncio.wait_for(self.entered.wait(), timeout)
        return self.calls[-1]

    async def hold(self, call: ActivityCall) -> None:
        self.calls.append(call)
        self.entered.set()
        try:
            await asyncio.wait_for(self.release.wait(), self.timeout)
        except TimeoutError:
            pass


def _ret_type(fn: Callable) -> Any:
    try:
        return typing.get_type_hints(fn).get("return")
    except Exception:
        return None


class Stubs:
    """Stub activities by real name, with recorded calls and per-name handlers."""

    def __init__(self, runtime_settings=None) -> None:
        self.real = activity_definitions()
        self.names: set[str] = set(activity_definitions(STUBBED_MODULES))
        self.calls: list[ActivityCall] = []
        self._handlers: dict[str, Callable[[ActivityCall], Any]] = {}
        self._gates: dict[str, Gate] = {}
        self.runtime_settings = runtime_settings

    # ── configuration ──
    def on(self, name: str, handler: Callable[[ActivityCall], Any]) -> None:
        self._check(name)
        self.names.add(name)
        self._handlers[name] = handler

    def gate(self, name: str, attempts: set[int] | None = None, timeout: float = 20.0) -> Gate:
        self._check(name)
        self._gates[name] = Gate(attempts, timeout)
        return self._gates[name]

    def release_all(self) -> None:
        for gate in self._gates.values():
            gate.release.set()

    def _check(self, name: str) -> None:
        if name not in self.real:
            raise AssertionError(f"no activity named {name!r} (known: {sorted(self.real)})")

    def calls_to(self, name: str, run_id=None) -> list[ActivityCall]:
        return [c for c in self.calls if c.name == name and (run_id is None or c.run_id == str(run_id))]

    def completed(self, name: str, run_id=None) -> list[ActivityCall]:
        return [c for c in self.calls_to(name, run_id) if c.completed]

    # ── worker wiring ──
    def swap(self, activities: list[Callable]) -> list[Callable]:
        return [self._stub(activity_name(fn)) if activity_name(fn) in self.names else fn for fn in activities]

    def _stub(self, name: str) -> Callable:
        stubs = self

        @activity.defn(name=name)
        async def stub(*args):
            info = activity.info()
            call = ActivityCall(name, args, info.attempt, info.workflow_id, info.heartbeat_timeout,
                                info.start_to_close_timeout)
            stubs.calls.append(call)
            try:
                gate = stubs._gates.get(name)
                if gate and gate.applies(call):
                    await gate.hold(call)
                handler = stubs._handlers.get(name) or stubs.default
                result = handler(call)
                if inspect.isawaitable(result):
                    result = await result
            except asyncio.CancelledError:
                call.cancelled = True
                raise
            call.completed = True
            return result

        return stub

    def default(self, call: ActivityCall, **overrides) -> Any:
        """Benign happy-path outputs, built from the real activity's return type (fields the type
        doesn't have are dropped, so overrides may name fields a later contract version adds)."""
        ret = _ret_type(self.real[call.name])
        if ret is None or ret is type(None) or not dataclasses.is_dataclass(ret):
            return None
        i = call.input
        url = i.get("job_url") or "https://jobs.harness.example/acme/1"
        job_id = url.rstrip("/").rsplit("/", 1)[-1]  # per-URL job identity, so title+company dedup can't collide
        schema = i.get("form_schema_dict") or {"fields": [STUB_FIELD], "page_url": url, "board": "generic"}
        mappings = i.get("field_mappings") or [STUB_MAPPING]
        mode = (self.runtime_settings.values.get("submission_mode") if self.runtime_settings else None) or "testing"
        defaults = {
            "page_url": url, "board": "generic", "account_email": APPLICANT_EMAIL,
            "job_description": "Synthetic job description for harness tests.", "canonical_url": url,
            "job_title": f"Software Engineer {job_id}", "job_company": "Acme", "job_location": "Boston, MA",
            "form_schema_dict": schema, "field_mappings": mappings,
            "written": {m["key"]: m["value"] for m in mappings if m.get("value")},
            "testing_mode": mode == "testing",  # pre-task-3 FillOutput; dropped by make() once removed
            "cover_letter_path": "", "cover_letter_text": "",
            "confirmation_url": url + "/thanks", "confirmation_text": "Thank you for applying",
        }
        return make(ret, **{**defaults, **overrides})


STUB_FIELD = {"key": "1", "label": "First name", "field_type": "text", "required": True, "options": []}
STUB_MAPPING = {"key": "1", "label": "First name", "field_type": "text", "source": "first_name",
                "value": "Jane", "confidence": 1.0}


class _ClientShim:
    def __init__(self, client) -> None:
        self._client = client

    async def connect(self, *args, **kwargs):
        return self._client


async def _idle(*args, **kwargs) -> None:
    await asyncio.Event().wait()


class WorkerHarness:
    """The real yahbah.worker.main() against the test server, with stubs swapped in."""

    def __init__(self, env: WorkflowEnvironment, stubs: Stubs, task_queue: str, monkeypatch) -> None:
        import yahbah.worker as worker_module
        from yahbah.config import settings

        self.module, self.stubs = worker_module, stubs
        self.workers: list[Worker] = []
        self._task: asyncio.Task | None = None
        monkeypatch.setattr(worker_module, "TemporalClient", _ClientShim(env.client))
        monkeypatch.setattr(worker_module, "Worker", self._make_worker)
        monkeypatch.setattr(worker_module, "run_status_poller", _idle, raising=False)
        monkeypatch.setattr(worker_module, "run_alert_poller", _idle, raising=False)
        monkeypatch.setattr(settings, "temporal_task_queue", task_queue)

    def _make_worker(self, client, **kwargs) -> Worker:
        kwargs["activities"] = self.stubs.swap(list(kwargs.get("activities") or []))
        worker = Worker(client, **kwargs)
        self.workers.append(worker)
        return worker

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, timeout: float = 15.0) -> None:
        """Run main() until its Worker is polling (its start-up writes are done by then)."""
        assert not self.running, "worker already running"
        before = len(self.workers)
        self._task = asyncio.create_task(self.module.main(), name="yahbah-worker-main")
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.workers) == before or not self.workers[-1].is_running:
            if self._task.done():
                self._task.result()  # main() failed: raise its error
                raise AssertionError("yahbah.worker.main() returned before its Worker started")
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("the worker did not start")
            await asyncio.sleep(0.02)

    async def stop(self, timeout: float = 60.0) -> None:
        """Graceful shutdown, as on Ctrl-C: cancel main() and let its shutdown path run."""
        if not self.running:
            return
        self._task.cancel()
        try:
            await asyncio.wait_for(self._task, timeout)
        except asyncio.CancelledError:
            pass

    async def restart(self) -> None:
        await self.stop()
        await self.start()


class Workflows:
    """The `workflows` fixture: stubs + worker + run helpers for one test."""

    def __init__(self, env: WorkflowEnvironment, db, stubs: Stubs, worker: WorkerHarness, task_queue: str) -> None:
        self.env, self.client, self.db, self.stubs, self.worker = env, env.client, db, stubs, worker
        self.task_queue = task_queue

    async def enqueue(self, url: str | None = None, **kwargs) -> uuid.UUID:
        """A run created the way POST /jobs creates one (yahbah.jobs.submit_job_for_application)."""
        from yahbah.jobs import submit_job_for_application

        url = url or f"https://jobs.harness.example/acme/{uuid.uuid4().hex[:10]}"
        run_id = await call_with_supported(submit_job_for_application, url, self.client,
                                           **{"origin": "manual", **kwargs})
        return uuid.UUID(str(run_id))

    async def handle(self, run_id) -> WorkflowHandle:
        row = await self.db.run(run_id)
        return self.client.get_workflow_handle(row["temporal_workflow_id"])

    async def workflow_status(self, run_id) -> WorkflowExecutionStatus:
        return (await (await self.handle(run_id)).describe()).status

    async def wait_closed(self, run_id, timeout: float = 30.0) -> WorkflowExecutionStatus:
        handle = await self.handle(run_id)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            status = (await handle.describe()).status
            if status != WorkflowExecutionStatus.RUNNING:
                return status
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(f"workflow of run {run_id} still running after {timeout}s; "
                                   f"statuses {await self.db.statuses_seen(run_id)}")
            await asyncio.sleep(0.1)

    async def wait_status(self, run_id, status: str, timeout: float = 20.0, occurrence: int = 1) -> None:
        """Until the run has been written `status` `occurrence` times; fails as soon as its workflow
        closes without that, naming what happened instead."""
        handle = await self.handle(run_id)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            seen = await self.db.statuses_seen(run_id)
            if seen.count(status) >= occurrence:
                return
            wf = (await handle.describe()).status
            if wf != WorkflowExecutionStatus.RUNNING:
                seen = await self.db.statuses_seen(run_id)
                if seen.count(status) >= occurrence:
                    return
                raise AssertionError(
                    f"run {run_id} never reached {status} (x{occurrence}): its workflow ended {wf.name}; "
                    f"statuses written {seen}; activities {[c.name for c in self.stubs.calls if c.run_id == str(run_id)]}")
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(f"run {run_id} not {status} after {timeout}s; statuses written {seen}")
            await asyncio.sleep(0.1)

    async def cleanup(self) -> None:
        """Terminate this test's still-running workflows, then stop the worker."""
        self.stubs.release_all()
        ids = {row["temporal_workflow_id"] for row in await self.db.runs() if row.get("temporal_workflow_id")}
        try:
            async for wf in self.client.list_workflows(f'TaskQueue="{self.task_queue}" AND ExecutionStatus="Running"'):
                ids.add(wf.id)
        except Exception:
            pass  # visibility query unsupported: the DB rows are enough
        for workflow_id in ids:
            handle = self.client.get_workflow_handle(workflow_id)
            try:
                if (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
                    await handle.terminate("test cleanup")
            except Exception:
                pass
        await self.worker.stop()
