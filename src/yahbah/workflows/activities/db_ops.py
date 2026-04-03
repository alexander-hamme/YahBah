"""
DB-only activities — thin wrappers that persist state/artifacts.
These are fast and don't do any I/O beyond the database.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from temporalio import activity

from yahbah.config import settings
from yahbah.db.session import AsyncSessionLocal
from yahbah.db.models import ApplicationRun, ApplicationArtifact, JobPosting
from yahbah.schemas import DuplicateCheckInput, DuplicateCheckOutput


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
async def store_credentials_activity(
    run_id: str, account_email: str, account_password: str
) -> None:
    """Persists the generated account credentials onto the ApplicationRun row."""
    async with AsyncSessionLocal() as session:
        run = await session.get(ApplicationRun, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"ApplicationRun {run_id} not found")
        run.account_email = account_email
        run.account_password = account_password
        run.updated_at = datetime.now(timezone.utc)
        await session.commit()


@activity.defn
async def mark_run_duplicate_activity(run_id: str, reason: str) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(ApplicationRun, uuid.UUID(run_id))
        if run is None:
            raise ValueError(f"ApplicationRun {run_id} not found")
        now = datetime.now(timezone.utc)
        run.status = "DUPLICATE"
        run.current_state = "DUPLICATE"
        run.error_message = reason
        run.updated_at = now
        run.completed_at = now
        await session.commit()


@activity.defn
async def check_duplicate_activity(input: DuplicateCheckInput) -> DuplicateCheckOutput:
    """
    1. Updates the JobPosting with extracted metadata (canonical_url, title,
       company, location) — filling in blanks without overwriting known values.
    2. Checks for an existing COMPLETED run on:
       a. Any posting with the same canonical_url.
       b. Any posting with the same (title + company + location) within
          settings.duplicate_window_days.
    Returns DuplicateCheckOutput indicating whether this is a duplicate.
    """
    async with AsyncSessionLocal() as session:
        # Load run to get job_posting_id
        run = await session.get(ApplicationRun, uuid.UUID(input.run_id))
        if run is None:
            raise ValueError(f"ApplicationRun {input.run_id} not found")

        posting = await session.get(JobPosting, run.job_posting_id)
        if posting is None:
            raise ValueError(f"JobPosting {run.job_posting_id} not found")

        # Fill in metadata blanks (don't overwrite values set by a previous run)
        if input.canonical_url and not posting.canonical_url:
            posting.canonical_url = input.canonical_url
        if input.job_title and not posting.title:
            posting.title = input.job_title
        if input.job_company and not posting.company:
            posting.company = input.job_company
        if input.job_location and not posting.location:
            posting.location = input.job_location
        if input.job_description and not posting.description:
            posting.description = input.job_description
        if input.salary_min and not posting.salary_min:
            posting.salary_min = input.salary_min
        if input.salary_max and not posting.salary_max:
            posting.salary_max = input.salary_max
        if input.technologies and not posting.technologies:
            posting.technologies = input.technologies
        if input.specialties and not posting.specialties:
            posting.specialties = input.specialties
        if input.company_website and not posting.company_website:
            posting.company_website = input.company_website
        await session.commit()

        window_start = datetime.now(timezone.utc) - timedelta(days=settings.duplicate_window_days)

        # ── Check A: canonical URL match ──────────────────────────────────────
        if input.canonical_url:
            result = await session.execute(
                select(ApplicationRun)
                .join(JobPosting, ApplicationRun.job_posting_id == JobPosting.id)
                .where(
                    JobPosting.canonical_url == input.canonical_url,
                    ApplicationRun.id != run.id,
                    ApplicationRun.status == "COMPLETED",
                )
                .limit(1)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return DuplicateCheckOutput(
                    is_duplicate=True,
                    reason=f"Already applied via canonical URL (run {existing.id})",
                    existing_run_id=str(existing.id),
                )

        # ── Check B: title + company + location within window ─────────────────
        if input.job_title and input.job_company and input.job_location:
            result = await session.execute(
                select(ApplicationRun)
                .join(JobPosting, ApplicationRun.job_posting_id == JobPosting.id)
                .where(
                    JobPosting.title == input.job_title,
                    JobPosting.company == input.job_company,
                    JobPosting.location == input.job_location,
                    ApplicationRun.id != run.id,
                    ApplicationRun.status == "COMPLETED",
                    ApplicationRun.completed_at >= window_start,
                )
                .limit(1)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return DuplicateCheckOutput(
                    is_duplicate=True,
                    reason=(
                        f"Already applied to '{input.job_title}' at '{input.job_company}' "
                        f"in '{input.job_location}' within the last "
                        f"{settings.duplicate_window_days} days (run {existing.id})"
                    ),
                    existing_run_id=str(existing.id),
                )

        return DuplicateCheckOutput(is_duplicate=False)


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
