"""
Temporal worker entrypoint.

Registers the ApplicationWorkflow and all activities, then polls the
task queue indefinitely.

Run with:
    uv run python -m yahbah.worker
"""
import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from temporalio.client import Client as TemporalClient
from temporalio.worker import Worker

from yahbah.agent.session import SessionRegistry  # first: sets browser-use env vars before it is imported
from yahbah.config import settings
from yahbah.db.models import ApplicationRun
from yahbah.db.session import AsyncSessionLocal
from yahbah.workflows.application import ApplicationWorkflow
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
    generate_cover_letter_activity,
)
from yahbah.gmail.poller import run_status_poller
from yahbah.gmail.alert_poller import run_alert_poller
from yahbah.workflows.activities.db_ops import (
    acquire_run_slot_activity,
    update_run_state_activity,
    mark_run_completed_activity,
    mark_run_duplicate_activity,
    mark_run_failed_activity,
    check_duplicate_activity,
    persist_artifact_activity,
    store_credentials_activity,
)


async def fail_incomplete_runs(reason: str) -> None:
    """Mark RUNNING runs as FAILED (their browser state is gone). PENDING runs are queued for a
    run slot; Temporal resumes their workflows, so they're left alone."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ApplicationRun).where(ApplicationRun.status == "RUNNING")
        )
        stale = result.scalars().all()
        if not stale:
            return

        now = datetime.now(timezone.utc)
        for run in stale:
            logger.warning(
                f"Marking run {run.id} as FAILED "
                f"(was {run.status}/{run.current_state})"
            )
            run.status = "FAILED"
            run.error_message = (
                f"{reason} (was {run.current_state or run.status})"
            )
            run.updated_at = now
            run.completed_at = now
        await session.commit()
        logger.info(f"Marked {len(stale)} incomplete run(s) as FAILED")


async def main() -> None:
    # Safety net: clean up runs orphaned by a previous hard kill
    await fail_incomplete_runs("Worker restarted")

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
                mark_run_completed_activity,
                mark_run_duplicate_activity,
                mark_run_failed_activity,
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
                generate_cover_letter_activity,
            ],
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
        # Graceful shutdown: mark in-flight runs before exiting
        await fail_incomplete_runs("Worker shutting down")
        await registry.close_all()

if __name__ == "__main__":
    asyncio.run(main())
