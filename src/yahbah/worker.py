"""
Temporal worker entrypoint.

Registers the ApplicationWorkflow and all activities, then polls the
task queue indefinitely.

Restart policy is resume (contract §7): the worker writes no run status when it starts or stops.
Temporal retries an activity interrupted by a restart on the next worker, which re-opens the
browser and checks the form; a run that had clicked submit only verifies, or ends SUBMIT_UNCERTAIN.

Run with:
    uv run python -m yahbah.worker
"""
import asyncio
from datetime import timedelta

from loguru import logger
from temporalio.client import Client as TemporalClient
from temporalio.worker import Worker

from yahbah.agent.session import SessionRegistry  # first: sets browser-use env vars before it is imported
from yahbah.config import settings
from yahbah.workflows.application import ApplicationWorkflow
from yahbah.workflows.activities.lease import RunLeaseInterceptor
from yahbah.workflows.activities.browser import (
    close_session_activity,
    discover_activity,
    fill_activity,
    map_form_activity,
    submit_activity,
)
from yahbah.workflows.activities.llm import (
    extract_job_metadata_activity,
    map_answers_activity,
    map_new_fields_activity,
    generate_cover_letter_activity,
)
from yahbah.gmail.poller import run_status_poller
from yahbah.gmail.alert_poller import run_alert_poller
from yahbah.workflows.activities.db_ops import (
    acquire_run_slot_activity,
    update_run_state_activity,
    enter_review_activity,
    mark_run_completed_activity,
    finish_run_activity,
    check_duplicate_activity,
    persist_artifact_activity,
    store_credentials_activity,
)

# On shutdown, running activities get this long to finish; the rest are cancelled and resumed
# (retried) by the next worker.
GRACEFUL_SHUTDOWN = timedelta(seconds=2)
# Heartbeats carry cancellation to an activity: send them at least this often.
MAX_HEARTBEAT_THROTTLE = timedelta(seconds=10)


async def main() -> None:
    # One browser-use session per run, shared across that run's browser activities
    registry = SessionRegistry.instance()

    try:
        temporal = await TemporalClient.connect(
            settings.temporal_host,
            namespace=settings.temporal_namespace,
        )
        logger.info(
            f"Worker connected to Temporal at {settings.temporal_host} "
            f"(namespace={settings.temporal_namespace}, "
            f"queue={settings.temporal_task_queue})"
        )

        worker = Worker(
            temporal,
            task_queue=settings.temporal_task_queue,
            workflows=[ApplicationWorkflow],
            activities=[
                # DB ops
                acquire_run_slot_activity,
                update_run_state_activity,
                enter_review_activity,
                mark_run_completed_activity,
                finish_run_activity,
                check_duplicate_activity,
                persist_artifact_activity,
                store_credentials_activity,
                # Browser
                discover_activity,
                map_form_activity,
                fill_activity,
                submit_activity,
                close_session_activity,
                # LLM
                extract_job_metadata_activity,
                map_answers_activity,
                map_new_fields_activity,
                generate_cover_letter_activity,
            ],
            interceptors=[RunLeaseInterceptor()],  # every activity attempt takes the run's lease first
            graceful_shutdown_timeout=GRACEFUL_SHUTDOWN,
            max_heartbeat_throttle_interval=MAX_HEARTBEAT_THROTTLE,
        )

        logger.info(f"Worker starting — polling task queue '{settings.temporal_task_queue}'")

        # Start background pollers
        status_poller_task = asyncio.create_task(run_status_poller())
        alert_poller_task = asyncio.create_task(
            run_alert_poller(temporal_client=temporal)
        )

        try:
            await worker.run()
        finally:
            status_poller_task.cancel()
            alert_poller_task.cancel()
            for task in (status_poller_task, alert_poller_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    finally:
        # Browsers die with the worker; the next worker re-opens them (resume policy).
        await registry.close_all()

if __name__ == "__main__":
    asyncio.run(main())
