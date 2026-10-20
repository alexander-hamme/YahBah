"""Probe C: what happens in-process when a non-heartbeating activity exceeds start_to_close?
Does the worker cancel attempt 1 locally, or does attempt 2 run alongside it?"""
import asyncio
import time
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

T0 = time.monotonic()
LIVE: set[int] = set()


def log(m):
    print(f"{time.monotonic() - T0:5.1f}s {m}", flush=True)


@activity.defn
async def slow() -> str:
    n = activity.info().attempt
    LIVE.add(n)
    log(f"attempt {n} start; live attempts={sorted(LIVE)}")
    try:
        await asyncio.sleep(6 if n == 1 else 1)
        log(f"attempt {n} finished normally; live attempts={sorted(LIVE)}")
        return f"attempt {n}"
    except asyncio.CancelledError:
        log(f"attempt {n} cancelled locally")
        raise
    finally:
        LIVE.discard(n)


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> str:
        return await workflow.execute_activity(
            slow, start_to_close_timeout=timedelta(seconds=2),
            retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(milliseconds=100)))


async def main():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path="/opt/homebrew/bin/temporal") as env:
        async with Worker(env.client, task_queue="c", workflows=[W], activities=[slow], workflow_runner=UnsandboxedWorkflowRunner()):
            r = await env.client.execute_workflow(W.run, id="probe-c", task_queue="c")
            log(f"workflow result: {r}")
            await asyncio.sleep(5)


asyncio.run(main())
