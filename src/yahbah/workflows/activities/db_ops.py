"""
DB-only activities — thin wrappers that persist run state and artifacts.

Every status or state write is fenced (contract §7): it applies only while the run belongs to the
calling workflow (temporal_workflow_id), the calling activity attempt still holds the run's lease
(no newer attempt took it, run_state.lease_clauses) and the run is not terminal; otherwise the
activity raises a non-retryable ApplicationError(type="superseded"). The write (re)takes the lease.
Every activity attempt also takes the lease when it starts (take_lease, from the worker's
lease.RunLeaseInterceptor). Outside an activity (direct calls in scripts and tests) only the
terminal-status fence applies.
"""
import asyncio
import re
import socket
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from loguru import logger
from sqlalchemy import case, func, or_, select, text, update
from temporalio import activity
from temporalio.exceptions import ApplicationError

from yahbah.config import settings
from yahbah.db.session import AsyncSessionLocal
from yahbah.db.models import ApplicationRun, ApplicationArtifact, JobPosting
from yahbah.run_state import (
    IN_FLIGHT, TERMINAL, Blocker, activity_owner, lease_clauses, lease_values, possibly_applied,
    record_possible_submission,
)
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


_RUN_SLOT_LOCK = 0x5961_6842  # pg advisory lock id guarding the slot check ("YahB")
SLOT_POLL_SECONDS = 2.0  # how often a queued run re-checks for a free slot


def superseded(run_id: str) -> ApplicationError:
    return ApplicationError(f"run {run_id} no longer belongs to this workflow, or has already ended",
                            type="superseded", non_retryable=True)


def _fence(run_id: str) -> list:
    """WHERE clauses of a fenced write: this run, owned by the calling workflow and leased to the
    calling attempt, not terminal."""
    clauses = [ApplicationRun.id == uuid.UUID(run_id), ApplicationRun.status.not_in(TERMINAL)]
    if owner := activity_owner():
        clauses += [ApplicationRun.temporal_workflow_id == owner[0], *lease_clauses(owner)]
    return clauses


async def fenced_update(run_id: str, **values) -> None:
    """UPDATE the run under the fence (keeping the caller's lease); superseded if it no longer applies."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(ApplicationRun).where(*_fence(run_id))
            .values(**values, **lease_values(activity_owner()), updated_at=func.now())
            .execution_options(synchronize_session=False))
        await session.commit()
    if result.rowcount != 1:
        raise superseded(run_id)


async def take_lease(run_id: str) -> None:
    """The calling activity attempt takes the run's lease (monotonic: never over a newer attempt)."""
    await fenced_update(run_id)


@activity.defn
async def acquire_run_slot_activity(run_id: str) -> None:
    """Wait until fewer than settings.max_concurrent_runs runs are RUNNING, then mark this one RUNNING.

    Only RUNNING holds a slot, so a run that ends or goes to review frees its slot without an
    explicit release. While it waits the run is PENDING/QUEUED (also when it comes back from an
    approved review). Local models are the bottleneck: concurrent runs would queue behind each other
    in Ollama (and swap models), so the default is one run at a time.
    """
    while True:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _RUN_SLOT_LOCK})
            run = (await session.execute(
                select(ApplicationRun).where(*_fence(run_id)).with_for_update())).scalar_one_or_none()
            if run is None:
                raise superseded(run_id)
            if run.status == "RUNNING":  # this activity's earlier attempt already took the slot
                return
            running = (await session.execute(
                select(func.count()).select_from(ApplicationRun).where(
                    ApplicationRun.status == "RUNNING", ApplicationRun.id != run.id)
            )).scalar_one()
            got_slot = running < settings.max_concurrent_runs
            status, state = ("RUNNING", "STARTING") if got_slot else ("PENDING", "QUEUED")
            if (run.status, run.current_state) != (status, state):
                run.status, run.current_state = status, state
                run.updated_at = datetime.now(timezone.utc)
            for column, value in lease_values(activity_owner()).items():
                setattr(run, column, value)
            await session.commit()
        if got_slot:
            return
        activity.heartbeat(f"queued behind {running} running run(s)")
        await asyncio.sleep(SLOT_POLL_SECONDS)


@activity.defn
async def update_run_state_activity(run_id: str, state: str) -> None:
    await fenced_update(run_id, current_state=state)


@activity.defn
async def enter_review_activity(run_id: str, review_round: int, review_deadline: str) -> None:
    """AWAITING_REVIEW for this round until review_deadline (ISO 8601, computed by the workflow);
    this frees the run slot (contract §4)."""
    await fenced_update(run_id, status="AWAITING_REVIEW", current_state="REVIEW", review_round=review_round,
                        review_deadline=datetime.fromisoformat(review_deadline))


@activity.defn
async def mark_run_completed_activity(run_id: str) -> None:
    await fenced_update(run_id, status="COMPLETED", current_state="DONE", failure_reason=None,
                        completed_at=func.now())


@activity.defn
async def finish_run_activity(run_id: str, status: str, failure_reason: str, error_message: str | None) -> None:
    """End the run as FAILED, CANCELLED, EXPIRED or DUPLICATE, with its reason. A run whose submit
    was attempted ends SUBMIT_UNCERTAIN instead, whatever ended it (contract §1); so does one that
    failed possibly_submitted, recorded or not: its claim is taken here if it is missing (§6)."""
    assert status in TERMINAL - {"COMPLETED", "SUBMIT_UNCERTAIN"}, status
    if failure_reason == "possibly_submitted":
        async with AsyncSessionLocal() as session:
            posting_id, workflow_id = (await session.execute(
                select(ApplicationRun.job_posting_id, ApplicationRun.temporal_workflow_id)
                .where(ApplicationRun.id == uuid.UUID(run_id)))).one()
        owner = activity_owner()
        await record_possible_submission(run_id, owner[0] if owner else workflow_id, posting_id)
        status = "SUBMIT_UNCERTAIN"
    await fenced_update(
        run_id,
        status=case((ApplicationRun.submit_attempted_at.is_not(None), "SUBMIT_UNCERTAIN"), else_=status),
        failure_reason=failure_reason, error_message=error_message, completed_at=func.now())


@activity.defn
async def store_credentials_activity(
    run_id: str, account_email: str, account_password: str | None = None
) -> None:
    """Persists the generated account credentials onto the ApplicationRun row."""
    await fenced_update(run_id, account_email=account_email, account_password=account_password)


async def _recent(session, blocker: Blocker, since: datetime) -> bool:
    """Did the blocking run apply (or is it applying) on or after `since`? A bare claim counts."""
    if blocker.run_id is None or blocker.status in IN_FLIGHT:
        return True
    at = (await session.execute(
        select(func.coalesce(ApplicationRun.submit_attempted_at, ApplicationRun.completed_at,
                             ApplicationRun.updated_at)).where(ApplicationRun.id == blocker.run_id)
    )).scalar_one_or_none()
    return at is None or at >= since


@activity.defn
async def check_duplicate_activity(input_: DuplicateCheckInput) -> DuplicateCheckOutput:
    """
    1. Updates the JobPosting with extracted metadata (canonical_url, title,
       company, location) — filling in blanks without overwriting known values.
    2. Asks possibly_applied() (contract §5) about every posting that is the same job:
       a. this posting, and any posting with the same canonical URL;
       b. any posting with the same title + company + location, if its blocking run
          applied within settings.duplicate_window_days.
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

        # The job board detected while discovering the form (always the latest detection)
        if input_.ats_type:
            posting.ats_type = input_.ats_type

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
        if input_.seniority_level and not posting.seniority_level:
            posting.seniority_level = input_.seniority_level
        if input_.experience_required and not posting.experience_required:
            posting.experience_required = input_.experience_required
        if input_.posted_date and not posting.posted_date:
            posting.posted_date = input_.posted_date

        # Fallback: derive company_website from URL if LLM didn't extract it
        if not posting.company_website:
            derived = _derive_company_website(posting.url)
            if derived:
                posting.company_website = derived

        await session.commit()

        def duplicate(blocker: Blocker, via: str) -> DuplicateCheckOutput:
            return DuplicateCheckOutput(
                is_duplicate=True,
                reason=f"Already applied via {via} (run {blocker.run_id}: {blocker.reason})",
                existing_run_id=str(blocker.run_id) if blocker.run_id else None,
            )

        # ── A: this posting, and postings with the same canonical URL ─────────
        same_url = [posting.id]
        if input_.canonical_url:
            same_url += (await session.execute(
                select(JobPosting.id).where(
                    or_(JobPosting.url == input_.canonical_url, JobPosting.canonical_url == input_.canonical_url),
                    JobPosting.id != posting.id)
            )).scalars().all()
        for posting_id in same_url:
            if blocker := await possibly_applied(session, posting_id, exclude_run_id=run.id):
                return duplicate(blocker, "canonical URL" if posting_id != posting.id else "this posting")

        # ── B: title + company + location within the window ───────────────────
        if input_.job_title and input_.job_company and input_.job_location:
            window_start = datetime.now(timezone.utc) - timedelta(days=settings.duplicate_window_days)
            same_job = (await session.execute(
                select(JobPosting.id).where(
                    JobPosting.title == input_.job_title,
                    JobPosting.company == input_.job_company,
                    JobPosting.location == input_.job_location,
                    JobPosting.id.not_in(same_url))
            )).scalars().all()
            for posting_id in same_job:
                blocker = await possibly_applied(session, posting_id, exclude_run_id=run.id)
                if blocker and await _recent(session, blocker, window_start):
                    return duplicate(blocker, f"'{input_.job_title}' at '{input_.job_company}' in "
                                              f"'{input_.job_location}' within the last "
                                              f"{settings.duplicate_window_days} days")

        return DuplicateCheckOutput(is_duplicate=False)


@activity.defn
async def persist_artifact_activity(
    run_id: str,
    artifact_type: str,
    path: str,
    metadata: dict | None = None,
) -> str:
    """Saves artifact record to DB; returns artifact id. Only while the run is in flight and, inside an
    activity, the caller still holds the run's lease (superseded otherwise): an attempt that lost the
    lease can't register evidence over its retry's."""
    async with AsyncSessionLocal() as session:
        if (await session.execute(
                select(ApplicationRun.id).where(*_fence(run_id)).with_for_update())).scalar_one_or_none() is None:
            raise superseded(run_id)
        artifact = ApplicationArtifact(
            run_id=uuid.UUID(run_id),
            artifact_type=artifact_type,
            path=path,
            artifact_metadata=metadata or {},
        )
        session.add(artifact)
        await session.commit()
        return str(artifact.id)
