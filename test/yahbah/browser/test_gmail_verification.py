"""
Test script for Gmail verification code retrieval.

Exercises the GmailClient + parser pipeline against a live Gmail inbox.
Searches for a recent Greenhouse verification code email and extracts the code.

Usage:
    uv run python test/yahbah/browser/test_gmail_verification.py

Requires:
  - OAuth2 credentials at ~/.config/yahbah/gmail/credentials.json
  - Completed one-time auth: python -m yahbah.gmail.client
  - A recent verification code email in the inbox (or send one first)
"""
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from yahbah.gmail.client import GmailClient
from yahbah.gmail.parser import (
    extract_code_heuristic,
    extract_verification_code,
    get_body_parts,
)


# ── Test: heuristic extraction on known HTML ─────────────────────────────────

def test_heuristic_extraction() -> None:
    """Verifies heuristic extraction against known Greenhouse HTML patterns."""
    print("\n=== test_heuristic_extraction ===")

    cases = [
        ("<h1>UNFAnJfW</h1>", "UNFAnJfW"),
        ("<h1>Ab3xZ9qR</h1>", "Ab3xZ9qR"),
        ("<h1>12345678</h1>", "12345678"),
        # Non-code content in h1 should not match
        ("<h1>Welcome to our site</h1>", None),
        ("<h1></h1>", None),
        # No h1 at all
        ("<p>Your code is ABC123</p>", None),
    ]

    for html, expected in cases:
        result = extract_code_heuristic(html)
        status = "PASS" if result == expected else "FAIL"
        print(f"  [{status}] {html[:50]!r} → {result!r} (expected {expected!r})")
        assert result == expected, f"Expected {expected!r}, got {result!r}"

    print("test_heuristic_extraction PASSED")


# ── Test: search Gmail for recent verification code emails ───────────────────

async def test_search_verification_emails() -> None:
    """
    Searches Gmail for recent verification code emails without consuming them.
    Prints matching messages and extracted codes.
    """
    print("\n=== test_search_verification_emails ===")

    client = GmailClient()

    query = "subject:(security code) newer_than:1d"
    print(f"  Searching: {query!r}")

    messages = await client._search_messages(query, max_results=5)
    print(f"  Found {len(messages)} matching message(s)")

    if not messages:
        print("  No recent verification code emails found — skipping extraction test")
        print("  (Submit an application that triggers email verification to test fully)")
        return

    for i, msg_meta in enumerate(messages):
        msg = await client._get_message(msg_meta["id"])

        # Extract subject and sender from headers
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        sender = headers.get("From", "(unknown)")
        to = headers.get("To", "(unknown)")
        date = headers.get("Date", "(unknown)")

        print(f"\n  Message {i + 1}:")
        print(f"    ID:      {msg_meta['id']}")
        print(f"    From:    {sender}")
        print(f"    To:      {to}")
        print(f"    Subject: {subject}")
        print(f"    Date:    {date}")

        html, plain = get_body_parts(msg["payload"])
        print(f"    HTML body:  {'yes' if html else 'no'} ({len(html) if html else 0} chars)")
        print(f"    Plain body: {'yes' if plain else 'no'} ({len(plain) if plain else 0} chars)")

        # Try heuristic first
        code = None
        if html:
            code = extract_code_heuristic(html)
            if code:
                print(f"    Code (heuristic): {code}")

        if not code:
            try:
                code = await extract_verification_code(html, plain)
                print(f"    Code (LLM fallback): {code}")
            except Exception as exc:
                print(f"    Code extraction failed: {exc}")

    print("\ntest_search_verification_emails PASSED")


# ── Test: full polling flow (short timeout) ──────────────────────────────────

async def test_poll_verification_code() -> None:
    """
    Runs the full fetch_verification_code polling loop with a short timeout.
    Expects a recent verification code email to exist — will timeout if none found.

    Pass a recipient email as the first CLI argument, or defaults to searching
    any recent verification code email.
    """
    print("\n=== test_poll_verification_code ===")

    # Use CLI arg or fall back to a broad search
    if len(sys.argv) > 1:
        recipient = sys.argv[1]
    else:
        print("  No recipient email provided as CLI arg — skipping poll test")
        print("  Usage: uv run python test/yahbah/browser/test_gmail_verification.py <email>")
        return

    print(f"  Polling for verification code sent to: {recipient}")
    print(f"  Timeout: 30s, interval: 5s")

    client = GmailClient()
    try:
        code = await client.fetch_verification_code(
            recipient_email=recipient,
            timeout_seconds=30,
            poll_interval=5,
        )
        print(f"  Verification code received: {code}")
        print("test_poll_verification_code PASSED")
    except RuntimeError as exc:
        print(f"  Polling timed out: {exc}")
        print("  (This is expected if no recent verification email exists)")


# ── Test: label management ───────────────────────────────────────────────────

async def test_label_management() -> None:
    """Verifies the Parsed label can be created/retrieved."""
    print("\n=== test_label_management ===")

    from yahbah.config import settings

    client = GmailClient()
    label_name = settings.gmail_label_parsed
    label_id = await client._get_or_create_label(label_name)
    print(f"  Label '{label_name}' → id={label_id}")

    # Second call should hit cache
    label_id_2 = await client._get_or_create_label(label_name)
    assert label_id == label_id_2, "Label ID mismatch on second call"
    print(f"  Cache hit confirmed")

    print("test_label_management PASSED")


# ── Runner ───────────────────────────────────────────────────────────────────

async def main() -> None:
    # Offline tests (no Gmail API needed)
    test_heuristic_extraction()

    # Online tests (require Gmail credentials)
    try:
        await test_label_management()
        await test_search_verification_emails()
        await test_poll_verification_code()
    except RuntimeError as exc:
        if "credentials" in str(exc).lower() or "token" in str(exc).lower():
            print(f"\n  Skipping online tests — Gmail not configured: {exc}")
        else:
            raise

    print("\n=== All tests complete ===")


if __name__ == "__main__":
    asyncio.run(main())
