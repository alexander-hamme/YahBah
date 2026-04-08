"""
Background Gmail poller for application status emails.

Reads emails from a designated Gmail folder (label), classifies them
via LLM, and stores status updates linked to the originating ApplicationRun.

Runs as a background asyncio task in the worker process.
"""
import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from yahbah.config import settings, get_runtime_settings
from yahbah.db.models import ApplicationRun, ApplicationStatusUpdate, GmailCheckpoint
from yahbah.db.session import AsyncSessionLocal
from yahbah.gmail.client import GmailClient, InvalidHistoryIdError
from yahbah.gmail.parser import (
    classify_status_email,
    get_body_parts,
)


async def run_status_poller() -> None:
    """Background loop: poll Gmail for status emails every N minutes."""
    # Brief startup delay so the worker can initialize first
    await asyncio.sleep(5)
    logger.info("[status-poller] Background poller started")

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
                if processed or skipped:
                    logger.info(
                        f"[status-poller] Cycle complete: "
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
    Single poll cycle: fetch new emails from the YahBah folder,
    classify them, and store status updates.

    Returns (processed_count, skipped_count).
    """
    gmail = GmailClient()

    inbox_label = rt.get("gmail_inbox_label", settings.gmail_inbox_label)
    status_label = rt.get("gmail_status_label", settings.gmail_status_label)

    # Resolve Gmail label IDs
    inbox_label_id = await gmail._get_or_create_label(inbox_label)
    status_label_id = await gmail._get_or_create_label(status_label)

    # Load or create checkpoint
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GmailCheckpoint).where(
                GmailCheckpoint.email == inbox_label
            )
        )
        checkpoint = result.scalar_one_or_none()

        if checkpoint is None:
            # First run: seed checkpoint with current historyId
            history_id = await gmail.get_current_history_id()
            checkpoint = GmailCheckpoint(
                email=inbox_label,
                history_id=history_id,
            )
            session.add(checkpoint)
            await session.commit()
            logger.info(
                f"[status-poller] Created checkpoint for '{inbox_label}' "
                f"at historyId={history_id}"
            )
            return 0, 0

    # Fetch new messages since last checkpoint
    try:
        messages, new_history_id = await gmail.get_history_since(
            checkpoint.history_id, label_ids=[inbox_label_id]
        )
    except InvalidHistoryIdError:
        logger.warning(
            "[status-poller] History ID expired — falling back to search"
        )
        messages = await _search_recent(gmail, inbox_label, parsed_label)
        new_history_id = await gmail.get_current_history_id()

    if not messages:
        # Update checkpoint even if no messages
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GmailCheckpoint).where(
                    GmailCheckpoint.email == inbox_label
                )
            )
            cp = result.scalar_one()
            cp.history_id = new_history_id
            await session.commit()
        return 0, 0

    # Process each message
    processed = 0
    skipped = 0

    for msg_meta in messages:
        msg_id = msg_meta.get("id") or msg_meta.get("message", {}).get("id")
        if not msg_id:
            continue

        try:
            result = await _process_message(
                gmail, msg_id, parsed_label_id, status_label_id
            )
            if result:
                processed += 1
            else:
                skipped += 1
        except Exception as exc:
            logger.warning(f"[status-poller] Failed to process message {msg_id}: {exc}")
            skipped += 1

    # Update checkpoint
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GmailCheckpoint).where(
                GmailCheckpoint.email == inbox_label
            )
        )
        cp = result.scalar_one()
        cp.history_id = new_history_id
        await session.commit()

    return processed, skipped


async def _search_recent(
    gmail: GmailClient, inbox_label: str, parsed_label: str
) -> list[dict]:
    """Fallback: search for unprocessed emails in the inbox label."""
    query = f"label:{inbox_label} -label:{parsed_label.replace('/', '-')}"
    results = await gmail._search_messages(query, max_results=50)
    return [{"id": m["id"]} for m in results]


async def _process_message(
    gmail: GmailClient,
    message_id: str,
    parsed_label_id: str,
    status_label_id: str,
) -> bool:
    """
    Process a single Gmail message. Returns True if a status update was created.
    """
    # Check if already processed (dedup by gmail_message_id)
    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(ApplicationStatusUpdate.id).where(
                ApplicationStatusUpdate.gmail_message_id == message_id
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False

    # Fetch full message
    msg = await gmail._get_message(message_id)
    headers = {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }

    to_addr = headers.get("to", "")
    subject = headers.get("subject", "")
    sender = headers.get("from", "")
    msg_epoch = int(msg.get("internalDate", "0")) // 1000
    email_date = datetime.fromtimestamp(msg_epoch, tz=timezone.utc)

    # Match to an ApplicationRun via the To: alias
    run_id = await _match_to_run(to_addr)
    if run_id is None:
        logger.debug(
            f"[status-poller] No matching run for to={to_addr!r}, "
            f"subject={subject!r} — labeling as parsed and skipping"
        )
        await gmail._add_label(message_id, parsed_label_id)
        return False

    # Extract body
    html, plain = get_body_parts(msg["payload"])
    body_text = plain or html or ""
    if not body_text:
        logger.debug(f"[status-poller] Empty body for message {message_id}")
        await gmail._add_label(message_id, parsed_label_id)
        return False

    # Strip HTML tags for plain text if we only have HTML
    if not plain and html:
        import re
        body_text = re.sub(r"<[^>]+>", " ", html)
        body_text = re.sub(r"\s+", " ", body_text).strip()

    # Classify via LLM
    classification = await classify_status_email(subject, sender, body_text)

    # Label as parsed regardless
    await gmail._add_label(message_id, parsed_label_id)

    if not classification.is_status_update:
        logger.debug(
            f"[status-poller] Not a status update: subject={subject!r} "
            f"(summary: {classification.summary})"
        )
        return False

    # Store the status update
    snippet = body_text[:500] if body_text else None
    async with AsyncSessionLocal() as session:
        update = ApplicationStatusUpdate(
            run_id=run_id,
            status_type=classification.status_type,
            subject=subject,
            sender=sender,
            summary=classification.summary,
            gmail_message_id=message_id,
            raw_snippet=snippet,
            email_date=email_date,
            confidence=classification.confidence,
        )
        session.add(update)
        await session.commit()

    # Also add the status label
    await gmail._add_label(message_id, status_label_id)

    logger.info(
        f"[status-poller] Status update: {classification.status_type} "
        f"for run {run_id} — {classification.summary}"
    )
    return True


async def _match_to_run(to_address: str) -> str | None:
    """
    Match a To: address to an ApplicationRun.account_email.
    Returns the run_id (as string) or None.
    """
    # Normalize: extract just the email from "Name <email>" format
    addr = to_address.strip()
    if "<" in addr:
        addr = addr.split("<")[-1].rstrip(">").strip()
    addr = addr.lower()

    if "+" not in addr:
        return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ApplicationRun.id).where(
                ApplicationRun.account_email == addr
            )
        )
        row = result.scalar_one_or_none()
        return str(row) if row else None
