"""
Async Gmail API client for email ingestion.

Uses the official Google Python client library with OAuth2 credentials.
All blocking API calls are wrapped with asyncio.to_thread() for async compat.

Two modes of operation:
  1. Verification code polling — searches recent messages matching criteria,
     extracts the code, and labels the message as processed.
  2. (Future) Incremental sync — uses history.list with a stored historyId
     checkpoint for efficient new-message detection.

Authentication:
  Requires a credentials.json (OAuth2 client secret) from Google Cloud Console
  and a token.json obtained via the initial consent flow.  Run the module
  directly (`python -m yahbah.gmail.client`) to complete the one-time auth.
"""
import asyncio
import os
import time
from pathlib import Path

from loguru import logger

from yahbah.config import settings
from yahbah.gmail.parser import extract_verification_code, get_body_parts

# Scopes: gmail.modify allows read + label management without full access.
_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _credentials_dir() -> Path:
    path = Path(settings.gmail_credentials_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_credentials():
    """
    Loads or refreshes OAuth2 credentials.  Raises RuntimeError if no
    valid token exists (run the one-time auth flow first).
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    cred_dir = _credentials_dir()
    token_path = cred_dir / "token.json"
    client_secret_path = cred_dir / "credentials.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
        return creds

    if not client_secret_path.exists():
        raise RuntimeError(
            f"Gmail credentials not found at {client_secret_path}. "
            "Download OAuth2 client secret from Google Cloud Console."
        )

    raise RuntimeError(
        f"Gmail token expired or missing at {token_path}. "
        "Run `python -m yahbah.gmail.client` to complete the OAuth flow."
    )


def _build_service():
    from googleapiclient.discovery import build

    creds = _build_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailClient:
    """
    Async Gmail API client.

    Usage::

        client = GmailClient()
        code = await client.fetch_verification_code("alias@gmail.com")
    """

    def __init__(self) -> None:
        self._service = _build_service()
        self._label_id_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_verification_code(
        self,
        recipient_email: str,
        timeout_seconds: int | None = None,
        poll_interval: int | None = None,
    ) -> str:
        """
        Polls Gmail for a verification code email sent to *recipient_email*.

        Searches for recent emails from Greenhouse containing "security code"
        in the subject, extracts the code, labels the message as processed,
        and returns the code string.

        Raises RuntimeError if no code is found within the timeout.
        """
        timeout = timeout_seconds or settings.gmail_poll_timeout_seconds
        interval = poll_interval or settings.gmail_poll_interval_seconds
        label = settings.gmail_label_parsed

        # Pre-resolve the "Parsed" label ID so we can exclude it in queries.
        label_id = await self._get_or_create_label(label)

        # Snapshot the current time as the cutoff — only accept emails that
        # arrived after the verification challenge was triggered.  We subtract
        # a buffer (90s) to account for clock skew and email transit.
        trigger_epoch = int(time.time()) - 90
        query = (
            f"to:{recipient_email} "
            f"subject:(security code) "
            f"after:{trigger_epoch} "
            f"-label:{label.replace('/', '-')}"
        )

        logger.info(
            f"[gmail] Polling for verification code — query={query!r}, "
            f"trigger_epoch={trigger_epoch}, timeout={timeout}s, interval={interval}s"
        )

        elapsed = 0
        while elapsed < timeout:
            messages = await self._search_messages(query, max_results=5)

            if messages:
                # Fetch full messages and sort by internalDate (newest first)
                full_messages = []
                for msg_meta in messages:
                    msg = await self._get_message(msg_meta["id"])
                    full_messages.append(msg)
                full_messages.sort(
                    key=lambda m: int(m.get("internalDate", "0")),
                    reverse=True,
                )

                for msg in full_messages:
                    msg_epoch = int(msg.get("internalDate", "0")) // 1000
                    msg_id = msg["id"]
                    headers = {
                        h["name"]: h["value"]
                        for h in msg.get("payload", {}).get("headers", [])
                    }
                    logger.debug(
                        f"[gmail] Candidate message {msg_id}: "
                        f"date={headers.get('Date', '?')}, "
                        f"epoch={msg_epoch}, subject={headers.get('Subject', '?')}"
                    )

                    # Extra guard: skip messages older than our trigger time
                    if msg_epoch < trigger_epoch:
                        logger.debug(f"[gmail] Skipping {msg_id} — older than trigger ({msg_epoch} < {trigger_epoch})")
                        continue

                    html, plain = get_body_parts(msg["payload"])
                    try:
                        code = await extract_verification_code(html, plain)
                    except Exception as exc:
                        logger.warning(
                            f"[gmail] Failed to extract code from message "
                            f"{msg_id}: {exc}"
                        )
                        continue

                    # Label as processed so we don't re-read it.
                    await self._add_label(msg_id, label_id)
                    logger.info(f"[gmail] Verification code extracted: {code} (from message {msg_id}, epoch={msg_epoch})")
                    return code

            logger.debug(
                f"[gmail] No matching messages yet — waiting {interval}s "
                f"({elapsed}/{timeout}s elapsed)"
            )
            await asyncio.sleep(interval)
            elapsed += interval

        raise RuntimeError(
            f"No verification code email found for {recipient_email} "
            f"after {timeout}s of polling"
        )

    # ------------------------------------------------------------------
    # Incremental sync (future use — job-alert ingestion)
    # ------------------------------------------------------------------

    async def get_history_since(
        self, start_history_id: str, label_ids: list[str] | None = None
    ) -> tuple[list[dict], str]:
        """
        Fetches message history since *start_history_id*.

        Returns (new_messages, latest_history_id).
        Raises InvalidHistoryIdError if the checkpoint is too old.
        """
        kwargs: dict = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
        }
        if label_ids:
            kwargs["labelIds"] = label_ids

        try:
            resp = await self._execute(
                self._service.users().history().list(**kwargs)
            )
        except Exception as exc:
            if "404" in str(exc) or "historyId" in str(exc).lower():
                raise InvalidHistoryIdError(start_history_id) from exc
            raise

        history_id = resp.get("historyId", start_history_id)
        messages: list[dict] = []
        for record in resp.get("history", []):
            for added in record.get("messagesAdded", []):
                messages.append(added["message"])

        return messages, history_id

    async def get_current_history_id(self) -> str:
        """Returns the current historyId for the authenticated user's mailbox."""
        profile = await self._execute(
            self._service.users().getProfile(userId="me")
        )
        return str(profile["historyId"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute(self, request):
        """Runs a blocking Google API request in a thread."""
        return await asyncio.to_thread(request.execute)

    async def _search_messages(self, query: str, max_results: int = 10) -> list[dict]:
        resp = await self._execute(
            self._service.users().messages().list(
                userId="me", q=query, maxResults=max_results
            )
        )
        return resp.get("messages", [])

    async def _get_message(self, message_id: str) -> dict:
        return await self._execute(
            self._service.users().messages().get(
                userId="me", id=message_id, format="full"
            )
        )

    async def _add_label(self, message_id: str, label_id: str) -> None:
        await self._execute(
            self._service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": [label_id]},
            )
        )

    async def _modify_labels(
        self,
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> None:
        """Add and/or remove labels from a message in a single API call."""
        body: dict = {}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids
        if body:
            await self._execute(
                self._service.users().messages().modify(
                    userId="me", id=message_id, body=body,
                )
            )

    async def _get_or_create_label(self, label_name: str) -> str:
        if label_name in self._label_id_cache:
            return self._label_id_cache[label_name]

        # Check existing labels
        resp = await self._execute(
            self._service.users().labels().list(userId="me")
        )
        for lbl in resp.get("labels", []):
            if lbl["name"] == label_name:
                self._label_id_cache[label_name] = lbl["id"]
                return lbl["id"]

        # Create the label
        created = await self._execute(
            self._service.users().labels().create(
                userId="me",
                body={
                    "name": label_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
        )
        label_id = created["id"]
        self._label_id_cache[label_name] = label_id
        logger.info(f"[gmail] Created label '{label_name}' (id={label_id})")
        return label_id


class InvalidHistoryIdError(Exception):
    """Raised when a historyId checkpoint is too old and Gmail rejects it."""

    def __init__(self, history_id: str) -> None:
        self.history_id = history_id
        super().__init__(f"Gmail historyId {history_id} is no longer valid")


# ------------------------------------------------------------------
# One-time OAuth flow — run directly to authenticate
# ------------------------------------------------------------------

def _run_oauth_flow() -> None:
    """Interactive OAuth2 consent flow. Run once to obtain token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    cred_dir = _credentials_dir()
    client_secret = cred_dir / "credentials.json"
    token_path = cred_dir / "token.json"

    if not client_secret.exists():
        print(f"ERROR: Place your OAuth2 client secret at {client_secret}")
        print("Download it from Google Cloud Console → APIs & Services → Credentials")
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), _SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json())
    print(f"Token saved to {token_path}")


if __name__ == "__main__":
    _run_oauth_flow()
