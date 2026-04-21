"""
Background Gmail poller for job alert emails.

Detects job alert emails via configurable sender patterns, extracts job
URLs, fetches each page for a description, scores relevance via LLM,
and inserts qualifying jobs into the scheduled_job table.  After each
poll cycle, promotes scheduled jobs whose delay has elapsed.

Runs as a background asyncio task in the worker process (alongside the
status poller).  Uses its own GmailCheckpoint (key ``alert_poller``).
"""
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select, func as sa_func

from yahbah.config import settings, get_runtime_settings
from yahbah.db.models import (
    ApplicationRun,
    GmailCheckpoint,
    JobPosting,
    ScheduledJob,
)
from yahbah.db.session import AsyncSessionLocal
from yahbah.gmail.alert_parser import (
    build_profile_summary,
    extract_job_urls,
    fetch_job_description,
    score_job,
    sender_is_alert,
)
from yahbah.gmail.client import GmailClient, InvalidHistoryIdError
from yahbah.gmail.parser import get_body_parts

_INBOX_LABEL_ID = "INBOX"
_CHECKPOINT_KEY = "alert_poller"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def trigger_alert_poll() -> tuple[int, int]:
    """Run a single poll cycle on demand. Returns (processed, skipped)."""
    rt = await get_runtime_settings()
    return await poll_for_alert_emails(rt)


async def run_alert_poller(temporal_client=None) -> None:
    """Background loop: poll Gmail for job alert emails every N minutes.

    *temporal_client* is the connected Temporal client from the worker,
    passed through for the promotion step.
    """
    await asyncio.sleep(8)  # stagger vs status poller (which sleeps 5s)
    logger.info("[alert-poller] Background poller started")

    # Startup poll
    try:
        rt = await get_runtime_settings()
        processed, skipped = await poll_for_alert_emails(rt)
        logger.info(
            f"[alert-poller] Startup poll: {processed} job(s) queued, "
            f"{skipped} skipped"
        )
        promoted = await _promote_ready_jobs(temporal_client)
        if promoted:
            logger.info(f"[alert-poller] Promoted {promoted} job(s)")
    except Exception as exc:
        logger.error(f"[alert-poller] Startup poll failed: {exc}")

    while True:
        try:
            rt = await get_runtime_settings()
            if not rt.get("job_alert_polling_enabled", False):
                await asyncio.sleep(60)
                continue

            interval = rt.get(
                "job_alert_polling_interval_minutes",
                settings.job_alert_polling_interval_minutes,
            )

            try:
                processed, skipped = await poll_for_alert_emails(rt)
                logger.info(
                    f"[alert-poller] Poll complete: "
                    f"{processed} job(s) queued, {skipped} skipped"
                )
                promoted = await _promote_ready_jobs(temporal_client)
                if promoted:
                    logger.info(f"[alert-poller] Promoted {promoted} job(s)")
            except Exception as exc:
                logger.error(f"[alert-poller] Error during poll cycle: {exc}")

            await asyncio.sleep(interval * 60)

        except asyncio.CancelledError:
            logger.info("[alert-poller] Shutting down")
            return
        except Exception as exc:
            logger.error(f"[alert-poller] Unexpected error: {exc}")
            await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Single poll cycle
# ---------------------------------------------------------------------------

async def poll_for_alert_emails(rt: dict) -> tuple[int, int]:
    """Single poll cycle. Returns (jobs_queued, skipped)."""
    gmail = GmailClient()

    alert_label = rt.get("job_alert_label", settings.job_alert_label)
    auto_archive = rt.get("job_alert_auto_archive", True)
    sender_patterns: list[str] = rt.get("job_alert_sender_patterns", [])
    min_score = rt.get("job_alert_min_match_score", settings.job_alert_min_match_score)
    delay_hours = rt.get(
        "job_alert_promotion_delay_hours",
        settings.job_alert_promotion_delay_hours,
    )

    if not sender_patterns:
        logger.debug("[alert-poller] No sender patterns configured — skipping")
        return 0, 0

    # Resolve label ID
    alert_label_id = await gmail._get_or_create_label(alert_label)

    # Load checkpoint
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GmailCheckpoint).where(
                GmailCheckpoint.email == _CHECKPOINT_KEY
            )
        )
        checkpoint = result.scalar_one_or_none()

    if checkpoint is None:
        # First run: check if a backfill date is configured
        backfill_since = rt.get("job_alert_backfill_since")
        if backfill_since:
            logger.info(
                f"[alert-poller] First run — backfilling alerts since {backfill_since}"
            )
            messages = await _search_alert_emails_since(
                gmail, sender_patterns, backfill_since
            )
        else:
            logger.info("[alert-poller] First run — seeding checkpoint (no backfill date set)")
            messages = []

        new_history_id = await gmail.get_current_history_id()
        async with AsyncSessionLocal() as session:
            session.add(GmailCheckpoint(
                email=_CHECKPOINT_KEY,
                history_id=new_history_id,
            ))
            await session.commit()

        if not messages:
            return 0, 0
    else:
        # Incremental fetch
        try:
            messages, new_history_id = await gmail.get_history_since(
                checkpoint.history_id
            )
        except InvalidHistoryIdError:
            logger.warning(
                "[alert-poller] History ID expired — falling back to search"
            )
            messages = await _search_alert_emails_since(
                gmail, sender_patterns,
                rt.get("job_alert_backfill_since"),
            )
            new_history_id = await gmail.get_current_history_id()

    if not messages:
        await _update_checkpoint(new_history_id)
        return 0, 0

    # Build profile text once for all scoring in this cycle
    profile_text = build_profile_summary()

    queued = 0
    skipped = 0

    for msg_meta in messages:
        msg_id = msg_meta.get("id") or msg_meta.get("message", {}).get("id")
        if not msg_id:
            continue

        try:
            count = await _process_message(
                gmail,
                msg_id,
                sender_patterns=sender_patterns,
                profile_text=profile_text,
                alert_label_id=alert_label_id,
                auto_archive=auto_archive,
                min_score=min_score,
                delay_hours=delay_hours,
            )
            queued += count
            if count == 0:
                skipped += 1
        except Exception as exc:
            logger.warning(
                f"[alert-poller] Failed to process message {msg_id}: {exc}"
            )
            skipped += 1

    await _update_checkpoint(new_history_id)
    return queued, skipped


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------

async def _process_message(
    gmail: GmailClient,
    message_id: str,
    *,
    sender_patterns: list[str],
    profile_text: str,
    alert_label_id: str,
    auto_archive: bool,
    min_score: int,
    delay_hours: int,
) -> int:
    """Process a single Gmail message. Returns number of jobs queued."""
    try:
        msg = await gmail._get_message(message_id)
    except Exception as exc:
        if "404" in str(exc) or "notFound" in str(exc):
            # Message was deleted/trashed since the history event — skip silently
            return 0
        raise
    headers = {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }

    sender = headers.get("from", "")
    subject = headers.get("subject", "")

    # Check sender against configured patterns
    source = sender_is_alert(sender, sender_patterns)
    if source is None:
        return 0

    # Extract HTML body
    html, _plain = get_body_parts(msg["payload"])
    if not html:
        logger.debug(f"[alert-poller] No HTML body in message {message_id}")
        return 0

    # Extract job URLs
    extracted = await extract_job_urls(html, source)
    if not extracted:
        return 0

    logger.info(
        f"[alert-poller] Found {len(extracted)} job URL(s) in alert from "
        f"{sender} — {subject!r}"
    )

    now = datetime.now(timezone.utc)
    promote_after = now + timedelta(hours=delay_hours)
    queued = 0

    for job in extracted:
        # Dedup: already in scheduled queue (active)?
        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(ScheduledJob.id).where(
                    ScheduledJob.job_url == job.url,
                    ScheduledJob.status.notin_(["SKIPPED", "EXPIRED"]),
                )
            )
            if existing.scalar_one_or_none() is not None:
                logger.debug(f"[alert-poller] Already scheduled: {job.url}")
                continue

        # Dedup: already applied to?
        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(ApplicationRun.id)
                .join(JobPosting)
                .where(
                    (JobPosting.url == job.url) | (JobPosting.canonical_url == job.url)
                )
                .where(ApplicationRun.status.notin_(["FAILED"]))
                .limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                logger.debug(f"[alert-poller] Already applied: {job.url}")
                continue

        # Fetch page and extract description for scoring
        description = await fetch_job_description(job.url)
        score_text = description or job.snippet
        match_score, match_rationale = None, None

        if score_text:
            match_score, match_rationale = await score_job(
                profile_text, score_text
            )

        # Determine status
        status = "SCHEDULED"
        if min_score > 0 and match_score is not None and match_score < min_score:
            status = "SKIPPED"
            logger.debug(
                f"[alert-poller] Auto-skipped {job.url} "
                f"(score {match_score} < {min_score})"
            )

        # Insert scheduled job
        async with AsyncSessionLocal() as session:
            sj = ScheduledJob(
                id=uuid.uuid4(),
                job_url=job.url,
                source=job.source,
                title=None,  # populated later by metadata extraction
                company=None,
                location=None,
                description=description,
                snippet=job.snippet,
                match_score=match_score,
                match_rationale=match_rationale,
                status=status,
                gmail_message_id=message_id,
                promote_after=promote_after,
            )
            session.add(sj)
            await session.commit()

        if status == "SCHEDULED":
            queued += 1
            logger.info(
                f"[alert-poller] Queued: {job.url} "
                f"(score={match_score}, promotes {promote_after.isoformat()})"
            )

    # Label the alert email
    add_labels = [alert_label_id]
    remove_labels: list[str] | None = None
    if auto_archive:
        remove_labels = [_INBOX_LABEL_ID]
    await gmail._modify_labels(message_id, add_labels, remove_labels)

    return queued


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------

async def _promote_ready_jobs(temporal_client=None) -> int:
    """Promote SCHEDULED jobs whose promote_after has passed.

    Uses the extracted submit_job_for_application() to create real
    ApplicationRuns and kick off Temporal workflows.
    """
    if temporal_client is None:
        return 0

    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ScheduledJob).where(
                ScheduledJob.status == "SCHEDULED",
                ScheduledJob.promote_after <= now,
                ScheduledJob.hold == False,  # noqa: E712
            )
        )
        ready = result.scalars().all()

    if not ready:
        return 0

    from yahbah.jobs import submit_job_for_application

    promoted = 0
    for job in ready:
        try:
            run_id = await submit_job_for_application(
                job.job_url, temporal_client
            )
            async with AsyncSessionLocal() as session:
                sj = await session.get(ScheduledJob, job.id)
                sj.status = "PROMOTED"
                sj.promoted_run_id = uuid.UUID(run_id)
                await session.commit()
            promoted += 1
            logger.info(
                f"[alert-poller] Promoted {job.job_url} → run {run_id}"
            )
        except Exception as exc:
            logger.warning(
                f"[alert-poller] Failed to promote {job.job_url}: {exc}"
            )

    return promoted


# ---------------------------------------------------------------------------
# Backfill search
# ---------------------------------------------------------------------------

async def _search_alert_emails_since(
    gmail: GmailClient,
    sender_patterns: list[str],
    since: str | None,
) -> list[dict]:
    """Search Gmail for alert emails from known senders since a given date.

    *since* is an ISO date string (e.g. "2026-04-10") or None (returns []).
    """
    if not since:
        return []

    from dateutil.parser import parse as parse_date
    try:
        dt = parse_date(since)
        epoch = int(dt.timestamp())
    except (ValueError, TypeError):
        logger.warning(f"[alert-poller] Invalid backfill date: {since!r}")
        return []

    # Build OR query from sender patterns.  Gmail search doesn't support
    # wildcards, so we extract the domain part from *@domain patterns.
    from_parts: list[str] = []
    for pat in sender_patterns:
        if pat.startswith("*@"):
            # *@linkedin.com → from:linkedin.com
            from_parts.append(f"from:{pat[2:]}")
        else:
            from_parts.append(f"from:{pat}")

    if not from_parts:
        return []

    query = f"({' OR '.join(from_parts)}) after:{epoch}"
    logger.info(f"[alert-poller] Backfill search: {query}")

    results = await gmail._search_messages(query, max_results=200)
    messages = [{"id": m["id"]} for m in results]
    logger.info(f"[alert-poller] Backfill found {len(messages)} email(s)")
    return messages


async def trigger_backfill(since: str) -> tuple[int, int]:
    """Delete the alert checkpoint and run a backfill poll from *since* date.

    Called by the API when the user sets a backfill date.
    """
    # Delete existing checkpoint so the poller does a fresh search
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GmailCheckpoint).where(
                GmailCheckpoint.email == _CHECKPOINT_KEY
            )
        )
        cp = result.scalar_one_or_none()
        if cp:
            await session.delete(cp)
            await session.commit()

    # Set the backfill date in runtime settings, then trigger a poll
    from yahbah.config import set_runtime_setting
    await set_runtime_setting("job_alert_backfill_since", since)

    return await trigger_alert_poll()


# ---------------------------------------------------------------------------
# Checkpoint management
# ---------------------------------------------------------------------------

async def _update_checkpoint(history_id: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GmailCheckpoint).where(
                GmailCheckpoint.email == _CHECKPOINT_KEY
            )
        )
        cp = result.scalar_one()
        cp.history_id = history_id
        await session.commit()
