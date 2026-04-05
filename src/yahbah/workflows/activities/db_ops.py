"""
DB-only activities — thin wrappers that persist state/artifacts.
These are fast and don't do any I/O beyond the database.
"""
import re
import socket
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from loguru import logger
from sqlalchemy import select
from temporalio import activity

from yahbah.config import settings
from yahbah.db.session import AsyncSessionLocal
from yahbah.db.models import ApplicationRun, ApplicationArtifact, JobPosting
from yahbah.schemas import DuplicateCheckInput, DuplicateCheckOutput


def _derive_company_website(url: str) -> str | None:
    """Best-effort domain derivation from a job posting URL.

    Tries to extract the company's website domain from the URL structure,
    then validates it resolves via DNS before returning.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")

    domain = None

    # boards.greenhouse.io/{slug}/jobs/... or job-boards.greenhouse.io/...
    if "greenhouse.io" in host:
        parts = path.split("/")
        if parts and parts[0]:
            slug = parts[0]
            # Common slug -> domain (try .com first, then .io, .co)
            domain = f"{slug}.com"
    # careers.{company}.com, jobs.{company}.com, www.{company}.com
    elif re.match(r"^(careers|jobs|www|apply|hire)\.", host):
        domain = re.sub(r"^(careers|jobs|www|apply|hire)\.", "", host)
    # {company}.com/careers/... or {company}.com/jobs/...
    elif host and not any(x in host for x in ["linkedin", "indeed", "glassdoor", "lever"]):
        domain = host

    if not domain:
        return None

    # Validate via DNS
    try:
        socket.getaddrinfo(domain, None, socket.AF_INET, socket.SOCK_STREAM)
        logger.debug(f"[derive_website] {url[:60]} -> {domain} (DNS OK)")
        return domain
    except socket.gaierror:
        # Try .io and .co as fallbacks for greenhouse slugs
        if "greenhouse.io" in host:
            for suffix in [".io", ".co", ".ai", ".dev"]:
                alt = domain.rsplit(".", 1)[0] + suffix
                try:
                    socket.getaddrinfo(alt, None, socket.AF_INET, socket.SOCK_STREAM)
                    logger.debug(f"[derive_website] {url[:60]} -> {alt} (DNS OK, fallback)")
                    return alt
                except socket.gaierror:
                    continue
        logger.debug(f"[derive_website] {url[:60]} -> {domain} (DNS failed)")
        return None


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
async def check_duplicate_activity(input_: DuplicateCheckInput) -> DuplicateCheckOutput:
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
        run = await session.get(ApplicationRun, uuid.UUID(input_.run_id))
        if run is None:
            raise ValueError(f"ApplicationRun {input_.run_id} not found")

        posting = await session.get(JobPosting, run.job_posting_id)
        if posting is None:
            raise ValueError(f"JobPosting {run.job_posting_id} not found")

        # Fill in metadata blanks (don't overwrite values set by a previous run)
        if input_.canonical_url and not posting.canonical_url:
            posting.canonical_url = input_.canonical_url
        if input_.job_title and not posting.title:
            posting.title = input_.job_title
        if input_.job_company and not posting.company:
            posting.company = input_.job_company
        if input_.job_location and not posting.location:
            posting.location = input_.job_location
        if input_.job_description and not posting.description:
            posting.description = input_.job_description
        if input_.salary_min and not posting.salary_min:
            posting.salary_min = input_.salary_min
        if input_.salary_max and not posting.salary_max:
            posting.salary_max = input_.salary_max
        if input_.technologies and not posting.technologies:
            posting.technologies = input_.technologies
        if input_.specialties and not posting.specialties:
            posting.specialties = input_.specialties
        if input_.company_website and not posting.company_website:
            posting.company_website = input_.company_website
        if input_.industry and not posting.industry:
            posting.industry = input_.industry
        if input_.company_description and not posting.company_description:
            posting.company_description = input_.company_description
        if input_.work_model and not posting.work_model:
            posting.work_model = input_.work_model
        if input_.posted_date and not posting.posted_date:
            posting.posted_date = input_.posted_date

        # Fallback: derive company_website from URL if LLM didn't extract it
        if not posting.company_website:
            derived = _derive_company_website(posting.url)
            if derived:
                posting.company_website = derived

        await session.commit()

        window_start = datetime.now(timezone.utc) - timedelta(days=settings.duplicate_window_days)

        # ── Check A: canonical URL match ──────────────────────────────────────
        if input_.canonical_url:
            result = await session.execute(
                select(ApplicationRun)
                .join(JobPosting, ApplicationRun.job_posting_id == JobPosting.id)
                .where(
                    JobPosting.canonical_url == input_.canonical_url,
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
        if input_.job_title and input_.job_company and input_.job_location:
            result = await session.execute(
                select(ApplicationRun)
                .join(JobPosting, ApplicationRun.job_posting_id == JobPosting.id)
                .where(
                    JobPosting.title == input_.job_title,
                    JobPosting.company == input_.job_company,
                    JobPosting.location == input_.job_location,
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
                        f"Already applied to '{input_.job_title}' at '{input_.job_company}' "
                        f"in '{input_.job_location}' within the last "
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
