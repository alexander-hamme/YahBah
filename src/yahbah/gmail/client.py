"""
Async Gmail API client for email ingestion.

Uses the official Google Python client library with OAuth2 credentials.
All blocking API calls are wrapped with asyncio.to_thread() for async compat.

Two modes of operation:
  1. Verification code polling — searches recent messages matching criteria and
     extracts the code (wait_for_verification_code); the caller marks the message
     processed once the page accepted the code (finalize_code_message).
  2. Incremental sync — history.list from a stored GmailCheckpoint.history_id
     (used by poller.py and alert_poller.py), falling back to search when the
     checkpoint has expired (InvalidHistoryIdError).

Authentication:
  Requires a credentials.json (OAuth2 client secret) from Google Cloud Console
  and a token.json obtained via the initial consent flow.  Run the module
  directly (`python -m yahbah.gmail.client`) to complete the one-time auth.
"""
import asyncio
import os
import time
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from yahbah.config import settings
from yahbah.gmail.parser import pick_verification_code

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
        found = await client.wait_for_verification_code("alias@gmail.com")
        ...  # enter found.code; once the page accepts it:
        await client.finalize_code_message(found.message_id)
    """

    def __init__(self) -> None:
        self._service = _build_service()
        self._label_id_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def wait_for_verification_code(
        self,
        recipient_email: str,
        since_epoch: int | None = None,
        subject_hint: str | None = None,
        timeout_seconds: int | None = None,
        poll_interval: int | None = None,
        *,
        code_pattern: str | None = None,
        exclude_message_ids: Collection[str] = (),
    ) -> "VerificationCode":
        """
        Polls Gmail for a verification code email sent to *recipient_email*
        (a per-application alias, so anything sent to it after the submit is
        about this application). Changes nothing in the mailbox.

        since_epoch: when the challenge was triggered (default: now).
        subject_hint: optional Gmail subject filter from the job board's profile
            (e.g. Greenhouse: "security code"); without it every new email to the
            alias is checked, and the LLM fallback only reads emails that mention a
            code keyword (code, verification, verify, security, one-time, OTP, passcode).
        code_pattern: the board's code shape, a regex the whole code must match
            (needed to accept codes without digits).
        exclude_message_ids: messages not to consider, e.g. one whose code the
            page rejected.

        Returns the code and the message it came from; see parser.py for what counts
        as a code. Raises RuntimeError if no code is found within the timeout.
        """
        timeout = timeout_seconds or settings.gmail_poll_timeout_seconds
        interval = poll_interval or settings.gmail_poll_interval_seconds
        label = settings.gmail_label_parsed

        # Only accept emails that arrived after the challenge was triggered, minus
        # a buffer (90s) for clock skew and email transit.
        trigger_epoch = (since_epoch or int(time.time())) - 90
        query = " ".join(filter(None, [
            f"to:{recipient_email}",
            f"subject:({subject_hint})" if subject_hint else None,
            f"after:{trigger_epoch}",
            f"-label:{label.replace('/', '-')}",
        ]))
        # Messages already checked that hold no code (e.g. confirmations) are left
        # unlabelled: the status poller still needs to see them.
        checked: set[str] = set(exclude_message_ids)

        logger.info(
            f"[gmail] Polling for verification code — query={query!r}, "
            f"trigger_epoch={trigger_epoch}, timeout={timeout}s, interval={interval}s"
        )

        elapsed = 0
        while elapsed < timeout:
            fresh = []
            for msg_meta in await self._search_messages(query, max_results=5):
                if msg_meta["id"] in checked:
                    continue
                msg = await self._get_message(msg_meta["id"])
                msg_epoch = int(msg.get("internalDate", "0")) // 1000
                # Extra guard: skip messages older than our trigger time
                if msg_epoch < trigger_epoch:
                    logger.debug(f"[gmail] Skipping {msg['id']} — older than trigger ({msg_epoch} < {trigger_epoch})")
                    checked.add(msg["id"])
                    continue
                fresh.append(msg)

            if fresh:
                found = await pick_verification_code(
                    fresh, code_pattern=code_pattern, require_keyword=subject_hint is None,
                )
                if found:
                    code, msg_id = found
                    logger.info(f"[gmail] Verification code found in message {msg_id} ({len(code)} characters)")
                    return VerificationCode(code=code, message_id=msg_id)
                checked.update(m["id"] for m in fresh)

            logger.debug(
                f"[gmail] No code email yet — waiting {interval}s "
                f"({elapsed}/{timeout}s elapsed)"
            )
            await asyncio.sleep(interval)
            elapsed += interval

        raise RuntimeError(
            f"No verification code email found for {recipient_email} "
            f"after {timeout}s of polling"
        )

    async def finalize_code_message(self, message_id: str) -> None:
        """
        Marks a verification-code email as processed, per the runtime setting
        ``gmail_verification_code_action``: "archive" = label Parsed and move to the
        YahBah folder, "delete" = trash, "nothing" = label Parsed only. Call it once
        the page has accepted the code. Safe to repeat.
        """
        from yahbah.config import get_runtime_settings
        rt = await get_runtime_settings()
        action = rt.get("gmail_verification_code_action", "nothing")
        if action == "delete":
            await self._trash_message(message_id)
            return
        label_id = await self._get_or_create_label(settings.gmail_label_parsed)
        if action == "archive":
            folder_label = rt.get("gmail_folder_label", settings.gmail_folder_label)
            folder_label_id = await self._get_or_create_label(folder_label)
            await self._modify_labels(
                message_id,
                add_label_ids=[label_id, folder_label_id],
                remove_label_ids=["INBOX"],
            )
        else:
            if action != "nothing":
                logger.warning(f"[gmail] Unknown verification code action: {action!r}, treating as 'nothing'")
            await self._add_label(message_id, label_id)

    async def fetch_verification_code(
        self,
        recipient_email: str,
        since_epoch: int | None = None,
        subject_hint: str | None = None,
        timeout_seconds: int | None = None,
        poll_interval: int | None = None,
        *,
        code_pattern: str | None = None,
    ) -> str:
        """
        wait_for_verification_code(), then finalize_code_message() straight away,
        before the page has seen the code. Returns the code.

        Superseded by the two-step form; remove once agent/submit.py uses it.
        """
        found = await self.wait_for_verification_code(
            recipient_email, since_epoch, subject_hint, timeout_seconds, poll_interval,
            code_pattern=code_pattern,
        )
        await self.finalize_code_message(found.message_id)
        return found.code

    # ------------------------------------------------------------------
    # Incremental sync (status + alert pollers)
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

        history_id = start_history_id
        messages: list[dict] = []
        while True:
            try:
                resp = await self._execute(
                    self._service.users().history().list(**kwargs)
                )
            except Exception as exc:
                if "404" in str(exc) or "historyId" in str(exc).lower():
                    raise InvalidHistoryIdError(start_history_id) from exc
                raise

            history_id = resp.get("historyId", history_id)
            for record in resp.get("history", []):
                for added in record.get("messagesAdded", []):
                    messages.append(added["message"])

            # Follow every page; otherwise a backlog is dropped while the
            # checkpoint still advances past it.
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
            kwargs["pageToken"] = page_token

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

    async def _trash_message(self, message_id: str) -> None:
        """Move a message to the trash."""
        await self._execute(
            self._service.users().messages().trash(userId="me", id=message_id)
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


@dataclass(frozen=True)
class VerificationCode:
    code: str
    message_id: str  # pass to finalize_code_message() once the page accepted the code


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
