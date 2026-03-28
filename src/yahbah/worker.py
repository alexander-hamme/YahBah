"""
Temporal worker entrypoint.

Registers the ApplicationWorkflow and all activities, then polls the
task queue indefinitely.

Run with:
    uv run python -m yahbah.worker
"""
import asyncio

from loguru import logger
from temporalio.client import Client as TemporalClient
from temporalio.worker import Worker

from yahbah.config import settings
from yahbah.browser.manager import BrowserRegistry
from yahbah.workflows.application import ApplicationWorkflow
from yahbah.workflows.activities.browser import (
    browser_open_and_auth_activity,
    browser_extract_activity,
    browser_fill_and_submit_activity,
)
from yahbah.workflows.activities.llm import (
    map_fields_activity,
    generate_cover_letter_activity,
)
from yahbah.workflows.activities.db_ops import (
    update_run_state_activity,
    mark_run_completed_activity,
    mark_run_failed_activity,
    persist_artifact_activity,
    store_credentials_activity,
)


async def main() -> None:
    # Start Playwright browser (shared across activities in this worker)
    registry = BrowserRegistry.instance()
    await registry.start()

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
                update_run_state_activity,
                mark_run_completed_activity,
                mark_run_failed_activity,
                persist_artifact_activity,
                store_credentials_activity,
                # Browser
                browser_open_and_auth_activity,
                browser_extract_activity,
                browser_fill_and_submit_activity,
                # LLM
                map_fields_activity,
                generate_cover_letter_activity,
            ],
        )

        logger.info("Worker starting — polling task queue '%s'", settings.temporal_task_queue)
        await worker.run()

    finally:
        await registry.stop()

if __name__ == "__main__":
    asyncio.run(main())
