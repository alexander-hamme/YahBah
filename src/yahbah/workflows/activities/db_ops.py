"""
DB-only activities — thin wrappers that persist state/artifacts.
These are fast and don't do any I/O beyond the database.
"""
import uuid
from datetime import datetime, timezone

from temporalio import activity

from yahbah.db.session import AsyncSessionLocal
from yahbah.db.models import ApplicationRun, ApplicationArtifact


@activity.defn
async def update_run_state_activity(run_id: str, state: str) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(ApplicationRun, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"ApplicationRun {run_id} not found")
        run.current_state = state
        run.status = "RUNNING"
        run.updated_at = datetime.now(timezone.utc)
        await session.commit()


@activity.defn
async def mark_run_completed_activity(run_id: str) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(ApplicationRun, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"ApplicationRun {run_id} not found")
        now = datetime.now(timezone.utc)
        run.status = "COMPLETED"
        run.current_state = "DONE"
        run.updated_at = now
        run.completed_at = now
        await session.commit()


@activity.defn
async def mark_run_failed_activity(run_id: str, error_message: str) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(ApplicationRun, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"ApplicationRun {run_id} not found")
        now = datetime.now(timezone.utc)
        run.status = "FAILED"
        run.error_message = error_message
        run.updated_at = now
        run.completed_at = now
        await session.commit()


@activity.defn
async def persist_artifact_activity(
    run_id: str,
    artifact_type: str,
    path: str,
    metadata: dict | None = None,
) -> str:
    """Saves artifact record to DB; returns artifact id."""
    async with AsyncSessionLocal() as session:
        artifact = ApplicationArtifact(
            run_id=uuid.UUID(run_id),
            artifact_type=artifact_type,
            path=path,
            artifact_metadata=metadata or {},
        )
        session.add(artifact)
        await session.commit()
        return str(artifact.id)
