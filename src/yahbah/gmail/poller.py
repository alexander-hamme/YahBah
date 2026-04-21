"""
Background Gmail poller for application status emails.

Reads new emails via the Gmail history API, filters to +alias recipients
(i.e. emails related to submitted applications), classifies them via LLM,
stores status updates linked to the originating ApplicationRun, and
optionally archives them to a designated folder based on per-status-type
settings.

Runs as a background asyncio task in the worker process.
"""
import asyncio
import re
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from yahbah.config import settings, get_runtime_settings
from yahbah.db.models import ApplicationRun, ApplicationStatusUpdate, GmailCheckpoint
from yahbah.db.session import AsyncSessionLocal
from yahbah.gmail.client import GmailClient, InvalidHistoryIdError
from yahbah.gmail.parser import classify_status_email, get_body_parts

# Gmail's built-in INBOX label ID (not a user-created label).
_INBOX_LABEL_ID = "INBOX"

# Checkpoint key — we use a fixed string since there's one poller per worker.
_CHECKPOINT_KEY = "status_poller"


async def trigger_poll() -> tuple[int, int]:
    """Run a single poll cycle on demand. Returns (processed, skipped)."""
    rt = await get_runtime_settings()
    return await poll_for_status_emails(rt)


async def run_status_poller() -> None:
    """Background loop: poll Gmail for status emails every N minutes."""
    await asyncio.sleep(5)
    logger.info("[status-poller] Background poller started")

    # Always run one poll on startup (regardless of enabled flag)
    try:
        rt = await get_runtime_settings()
        processed, skipped = await poll_for_status_emails(rt)
        logger.info(
            f"[status-poller] Startup poll: {processed} update(s), {skipped} skipped"
        )
    except Exception as exc:
        logger.error(f"[status-poller] Startup poll failed: {exc}")

    while True:
        try:
            rt = await get_runtime_settings()
            if not rt.get("gmail_status_polling_enabled", False):
                await asyncio.sleep(60)
                continue

            interval = rt.get(
                "gmail_status_polling_interval_minutes",
                settings.gmail_status_polling_interval_minutes,
            )

            try:
                processed, skipped = await poll_for_status_emails(rt)
                logger.info(
                    f"[status-poller] Poll complete: "
                    f"{processed} status update(s), {skipped} skipped"
                )
            except Exception as exc:
                logger.error(f"[status-poller] Error during poll cycle: {exc}")

            await asyncio.sleep(interval * 60)

        except asyncio.CancelledError:
            logger.info("[status-poller] Shutting down")
            return
        except Exception as exc:
            logger.error(f"[status-poller] Unexpected error: {exc}")
            await asyncio.sleep(60)


async def poll_for_status_emails(rt: dict) -> tuple[int, int]:
    """
    Single poll cycle. Returns (processed_count, skipped_count).
    """
    gmail = GmailClient()

    folder_label = rt.get("gmail_folder_label", settings.gmail_folder_label)
    status_label = rt.get("gmail_status_label", settings.gmail_status_label)
    auto_archive: dict[str, bool] = rt.get(
        "gmail_auto_archive", settings.gmail_auto_archive
    )

    # Resolve label IDs we'll need
    folder_label_id = await gmail._get_or_create_label(folder_label)
    status_label_id = await gmail._get_or_create_label(status_label)

    # Load checkpoint
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GmailCheckpoint).where(
                GmailCheckpoint.email == _CHECKPOINT_KEY
            )
        )
        checkpoint = result.scalar_one_or_none()

    if checkpoint is None:
        # First run: backfill by searching for emails since the oldest
        # submitted application, then create the checkpoint at "now".
        logger.info("[status-poller] First run — backfilling existing status emails")
        messages = await _search_since_oldest_run(gmail)
        new_history_id = await gmail.get_current_history_id()

        # Create checkpoint so subsequent polls use incremental history
        async with AsyncSessionLocal() as session:
            session.add(GmailCheckpoint(
                email=_CHECKPOINT_KEY,
                history_id=new_history_id,
            ))
            await session.commit()
    else:
        # Normal: incremental fetch since last checkpoint
        try:
            messages, new_history_id = await gmail.get_history_since(
                checkpoint.history_id
            )
        except InvalidHistoryIdError:
            logger.warning(
                "[status-poller] History ID expired — falling back to search"
            )
            messages = await _search_since_oldest_run(gmail)
            new_history_id = await gmail.get_current_history_id()

    if not messages:
        await _update_checkpoint(new_history_id)
        return 0, 0

    processed = 0
    skipped = 0

    for msg_meta in messages:
        msg_id = msg_meta.get("id") or msg_meta.get("message", {}).get("id")
        if not msg_id:
            continue

        try:
            ok = await _process_message(
                gmail, msg_id,
                folder_label_id=folder_label_id,
                status_label_id=status_label_id,
                auto_archive=auto_archive,
            )
            if ok:
                processed += 1
            else:
                skipped += 1
        except Exception as exc:
            logger.warning(
                f"[status-poller] Failed to process message {msg_id}: {exc}"
            )
            skipped += 1

    await _update_checkpoint(new_history_id)
    return processed, skipped


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


async def _search_since_oldest_run(gmail: GmailClient) -> list[dict]:
    """
    Search for emails to the profile address (both base and +alias)
    going back to the oldest submitted application's timestamp.
    Used for first-run backfill and when the history checkpoint expires.
    """
    from sqlalchemy import func as sa_func
    from yahbah.config import load_prompts_config

    base_email = load_prompts_config().get("profile", {}).get("email", "")
    if not base_email or "@" not in base_email:
        return []

    # Find the oldest application's created_at timestamp
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(sa_func.min(ApplicationRun.created_at))
        )
        oldest = result.scalar_one_or_none()

    if oldest is None:
        logger.info("[status-poller] No applications found — nothing to backfill")
        return []

    logger.info(
        f"[status-poller] Searching for emails since oldest application: "
        f"{oldest.strftime('%b %d, %Y %I:%M %p')}"
    )
    epoch = int(oldest.timestamp())
    local, domain = base_email.split("@", 1)

    # Search for emails to both the base address and any +alias variants
    queries = [
        f"to:{base_email} after:{epoch}",
        f"to:{local}+*@{domain} after:{epoch}",
    ]

    seen_ids: set[str] = set()
    messages: list[dict] = []
    for query in queries:
        results = await gmail._search_messages(query, max_results=100)
        for m in results:
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                messages.append({"id": m["id"]})

    logger.info(
        f"[status-poller] Search found {len(messages)} email(s) "
        f"since {oldest.isoformat()}"
    )
    return messages


async def _process_message(
    gmail: GmailClient,
    message_id: str,
    folder_label_id: str,
    status_label_id: str,
    auto_archive: dict[str, bool],
) -> bool:
    """
    Process a single Gmail message. Returns True if a status update was created.
    """
    # Dedup: skip if already processed
    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(ApplicationStatusUpdate.id).where(
                ApplicationStatusUpdate.gmail_message_id == message_id
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False

    # Fetch full message (may 404 if deleted since history event)
    try:
        msg = await gmail._get_message(message_id)
    except Exception as exc:
        if "404" in str(exc) or "notFound" in str(exc):
            return False
        raise
    headers = {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }

    to_addr = headers.get("to", "")
    subject = headers.get("subject", "")
    sender = headers.get("from", "")
    msg_epoch = int(msg.get("internalDate", "0")) // 1000
    email_date = datetime.fromtimestamp(msg_epoch, tz=timezone.utc)

    # Try matching by +alias first (fast, exact)
    normalized_to = _extract_email(to_addr)
    run_id: str | None = None
    if "+" in normalized_to:
        run_id = await _match_by_alias(normalized_to)

    # Extract body text (needed for both classification and fallback matching)
    html, plain = get_body_parts(msg["payload"])
    body_text = plain or ""
    if not body_text and html:
        body_text = re.sub(r"<[^>]+>", " ", html)
        body_text = re.sub(r"\s+", " ", body_text).strip()
    if not body_text:
        return False

    # Classify via LLM (also extracts company_name for fallback matching)
    classification = await classify_status_email(subject, sender, body_text)

    if not classification.is_status_update:
        logger.debug(
            f"[status-poller] Not a status update: subject={subject!r}"
        )
        return False

    # Fallback: match by company name from the LLM classification
    if run_id is None and classification.company_name:
        run_id = await _match_by_company(classification.company_name)

    if run_id is None:
        logger.debug(
            f"[status-poller] No matching run for to={normalized_to!r}, "
            f"company={classification.company_name!r}, subject={subject!r}"
        )
        return False

    # Store the status update
    async with AsyncSessionLocal() as session:
        update = ApplicationStatusUpdate(
            run_id=run_id,
            status_type=classification.status_type,
            subject=subject,
            sender=sender,
            summary=classification.summary,
            gmail_message_id=message_id,
            raw_snippet=body_text[:500],
            email_date=email_date,
            confidence=classification.confidence,
        )
        session.add(update)
        await session.commit()

    # Apply labels: always add YahBah/Status
    add_labels = [status_label_id]
    remove_labels: list[str] = []

    # Auto-archive: move to YahBah folder and out of inbox if configured
    should_archive = auto_archive.get(classification.status_type, False)
    if should_archive:
        add_labels.append(folder_label_id)
        remove_labels.append(_INBOX_LABEL_ID)

    await gmail._modify_labels(message_id, add_labels, remove_labels or None)

    logger.info(
        f"[status-poller] {classification.status_type} for run {run_id}"
        f"{' (archived)' if should_archive else ' (kept in inbox)'}"
        f" — {classification.summary}"
    )
    return True


def _extract_email(addr: str) -> str:
    """Extract bare email from 'Name <email>' format and lowercase."""
    addr = addr.strip()
    if "<" in addr:
        addr = addr.split("<")[-1].rstrip(">").strip()
    return addr.lower()


async def _match_by_alias(email: str) -> str | None:
    """Match email alias to ApplicationRun.account_email. Returns run_id or None."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ApplicationRun.id).where(
                ApplicationRun.account_email == email
            )
        )
        row = result.scalar_one_or_none()
        return str(row) if row else None


async def _match_by_company(company_name: str) -> str | None:
    """
    Fallback: match by company name from the email to JobPosting.company.
    Returns the most recent completed run_id, or None.
    """
    from yahbah.db.models import JobPosting

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ApplicationRun.id)
            .join(JobPosting)
            .where(JobPosting.company.ilike(f"%{company_name}%"))
            .where(ApplicationRun.status.in_(["COMPLETED", "FAILED"]))
            .order_by(ApplicationRun.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return str(row) if row else None
