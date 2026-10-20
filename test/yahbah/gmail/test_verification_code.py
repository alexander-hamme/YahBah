"""Verification-code extraction and retrieval: what counts as a code, which email wins, and
when the mailbox is changed. Offline: Gmail calls and the LLM are faked."""
import asyncio
import base64
import time
from types import SimpleNamespace

import pytest
from loguru import logger

from yahbah.agent.boards import GREENHOUSE
from yahbah.gmail import client as client_module
from yahbah.gmail import parser
from yahbah.gmail.client import GmailClient, VerificationCode
from yahbah.gmail.parser import extract_code_heuristic, extract_verification_code, pick_verification_code

GREENHOUSE_SHAPE = GREENHOUSE.email_code_pattern


# ── The heuristic ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("html", [
    "<h1>Thanks</h1><p>We received your application.</p>",
    "<h1>Welcome</h1><p>Your account is ready.</p>",
    "<h1>Confirmation</h1>",
    "<h1>Acme</h1><p>Thanks for applying to Acme.</p>",
    "<h1>AcmeRobotics</h1><p>Your application to AcmeRobotics was received.</p>",
    "<p>Application ID 48213 received.</p>",
    "<p>Thank you for applying (Req APP-48213). Please review our code of conduct.</p>",
    "<p>Thanks!</p><footer>© 2026 Acme · Privacy · Security</footer>",
    "<p>Verify your email: https://acme.example.com/verify?token=abc123def</p>",
    "<p>Questions about your security? Write to help123@acme.example.com</p>",
])
def test_heuristic_rejects_non_codes(html):
    assert extract_code_heuristic(html) is None
    assert extract_code_heuristic(html, code_pattern=GREENHOUSE_SHAPE) is None


@pytest.mark.parametrize("html, expected", [
    ("<h1>Acme</h1><p>Your security code is 739201</p>", "739201"),
    ("<p>Here is your code: 739201</p>", "739201"),
    ("<p>739201 is your one-time passcode. It expires in 10 minutes.</p>", "739201"),
    ("<p>Thanks for applying (Job 48213).</p><p>Your verification code is <b>A1B2C3</b></p>", "A1B2C3"),
    ("<p>Copy and paste this code into the security code field on your application:</p>"
     "<h1>Ab3xZ9qR</h1>", "Ab3xZ9qR"),
])
def test_heuristic_accepts_codes_near_code_keywords(html, expected):
    assert extract_code_heuristic(html) == expected


def test_plain_text_body():
    assert extract_code_heuristic(None, "Hi,\n\nYour verification code:\n\n  739201\n\nThanks") == "739201"


@pytest.mark.parametrize("code", ["UNFAnJfW", "qRtYxZaB", "AbCdEfGh"])
def test_greenhouse_letters_only_code_alone_in_h1_needs_the_declared_pattern(code):
    html = f"<p>Copy and paste this code into the security code field on your application:</p><h1>{code}</h1>"
    assert GREENHOUSE.email_code_pattern == r"[A-Za-z0-9]{8}"
    assert extract_code_heuristic(html, code_pattern=GREENHOUSE.email_code_pattern) == code
    assert extract_code_heuristic(html) is None


def test_letters_only_code_needs_the_board_shape_and_a_heading_of_its_own():
    html = ("<p>Copy and paste this code into the security code field on your application:</p>"
            "<h1>UNFAnJfW</h1>")
    assert extract_code_heuristic(html) is None
    assert extract_code_heuristic(html, code_pattern=GREENHOUSE_SHAPE) == "UNFAnJfW"
    # not set apart: running text
    assert extract_code_heuristic("<p>Your security code is UNFAnJfW</p>", code_pattern=GREENHOUSE_SHAPE) is None
    # words and names of the right length are not codes
    for word in ["Security", "SECURITY", "Received"]:
        assert extract_code_heuristic(f"<p>Enter the security code below.</p><h1>{word}</h1>",
                                      code_pattern=GREENHOUSE_SHAPE) is None
    assert extract_code_heuristic("<p>Thanks for applying to LinkedIn. Enter the security code.</p>"
                                  "<h1>LinkedIn</h1>", code_pattern=GREENHOUSE_SHAPE) is None


def test_a_declared_shape_excludes_other_codes():
    assert extract_code_heuristic("<p>Your code is 739201</p>", code_pattern=GREENHOUSE_SHAPE) is None


def test_heading_code_with_the_keyword_only_in_the_subject():
    html = "<p>" + "Some long preamble text. " * 10 + "</p><h1>Ab3xZ9qR</h1>"
    assert extract_code_heuristic(html) is None
    assert extract_code_heuristic(html, subject="Security code for your application") == "Ab3xZ9qR"


# ── Choosing among emails ────────────────────────────────────────────────────

def message(message_id: str, *, subject: str, html: str, age_seconds: int = 0) -> dict:
    return {
        "id": message_id,
        "internalDate": str(int((time.time() - age_seconds) * 1000)),
        "payload": {
            "mimeType": "text/html",
            "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": "Acme <no-reply@acme.example>"}],
            "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()},
        },
    }


CONFIRMATION = message("m-confirm", subject="Thanks for applying to Acme",
                       html="<h1>Thanks</h1><p>We received your application for Engineer (Req 48213).</p>")
CONFIRMATION_WITH_NUMBER = message("m-confirm2", subject="Application received",
                                   html="<p>Thank you! Your application reference code is 48213.</p>")
CODE = message("m-code", subject="Your verification code", html="<p>Your code is 739201</p>", age_seconds=5)


@pytest.fixture
def llm(monkeypatch):
    fake = SimpleNamespace(calls=[], answer=None)

    async def extract_code_llm(text):
        fake.calls.append(text)
        return fake.answer

    monkeypatch.setattr(parser, "extract_code_llm", extract_code_llm)
    return fake


@pytest.mark.parametrize("messages", [
    [CONFIRMATION, CODE], [CODE, CONFIRMATION],
    [CONFIRMATION_WITH_NUMBER, CODE], [CODE, CONFIRMATION_WITH_NUMBER],
])
async def test_code_email_wins_over_a_confirmation_arriving_with_it(llm, messages):
    assert await pick_verification_code(messages) == ("739201", "m-code")
    assert llm.calls == []


async def test_llm_fallback_answer_must_be_a_code_in_that_email(llm):
    msg = message("m1", subject="Verify your email", html="<p>Enter this number on the site: 739201</p>")
    for answer in ["Thanks", "123456", "Acme"]:
        llm.answer = answer
        assert await pick_verification_code([msg]) is None
    llm.answer = "739201"
    assert await pick_verification_code([msg]) == ("739201", "m1")


async def test_llm_fallback_only_reads_emails_that_mention_a_code(llm):
    llm.answer = "48213"
    welcome = message("m1", subject="Welcome to Acme", html="<p>Your account number is 48213.</p>")
    assert await pick_verification_code([welcome]) is None
    assert llm.calls == []
    # With a board subject filter in the Gmail query, every result is read
    assert await pick_verification_code([welcome], require_keyword=False) == ("48213", "m1")


async def test_single_email_extraction_uses_the_same_rules(llm):
    llm.answer = "Thanks"
    assert await extract_verification_code("<h1>Thanks</h1><p>Your code will follow.</p>", None) is None
    assert await extract_verification_code("<h1>Acme</h1><p>Your security code is 739201</p>", None) == "739201"


# ── GmailClient: retrieval and mailbox changes ───────────────────────────────

class FakeGmail(GmailClient):
    """GmailClient over canned messages; records searches and every mailbox change."""

    def __init__(self, messages: list[dict]):
        self.messages = messages
        self.queries: list[str] = []
        self.changes: list[tuple] = []
        self._label_id_cache = {}

    async def _search_messages(self, query, max_results=10):
        self.queries.append(query)
        return [{"id": m["id"]} for m in self.messages]

    async def _get_message(self, message_id):
        return next(m for m in self.messages if m["id"] == message_id)

    async def _get_or_create_label(self, label_name):
        return f"id:{label_name}"

    async def _modify_labels(self, message_id, add_label_ids=None, remove_label_ids=None):
        self.changes.append(("modify", message_id, add_label_ids, remove_label_ids))

    async def _add_label(self, message_id, label_id):
        self.changes.append(("label", message_id, label_id))

    async def _trash_message(self, message_id):
        self.changes.append(("trash", message_id))


@pytest.fixture
def no_wait(monkeypatch):
    async def sleep(seconds):
        await asyncio.sleep(0)
    monkeypatch.setattr(client_module, "asyncio", SimpleNamespace(sleep=sleep))


@pytest.fixture
def code_action(monkeypatch):
    rt = {"gmail_verification_code_action": "archive", "gmail_folder_label": "YahBah"}

    async def get_runtime_settings():
        return rt

    monkeypatch.setattr("yahbah.config.get_runtime_settings", get_runtime_settings)
    return rt


@pytest.fixture
def logs():
    records: list[str] = []
    handler = logger.add(lambda m: records.append(str(m)), level="DEBUG")
    yield records
    logger.remove(handler)


async def test_wait_returns_code_and_message_without_touching_the_mailbox(llm, no_wait, logs):
    gmail = FakeGmail([CONFIRMATION, CODE])

    found = await gmail.wait_for_verification_code("me+acme1@example.com", since_epoch=int(time.time()) - 30,
                                                   timeout_seconds=7, poll_interval=7)

    assert found == VerificationCode(code="739201", message_id="m-code")
    assert gmail.changes == []
    assert not any("739201" in line for line in logs)


async def test_finalize_applies_the_configured_action_to_that_message_only(code_action):
    gmail = FakeGmail([CONFIRMATION, CODE])

    await gmail.finalize_code_message("m-code")
    assert gmail.changes == [("modify", "m-code", ["id:YahBah/Parsed", "id:YahBah"], ["INBOX"])]

    code_action["gmail_verification_code_action"] = "delete"
    await gmail.finalize_code_message("m-code")
    assert gmail.changes[-1] == ("trash", "m-code")

    code_action["gmail_verification_code_action"] = "nothing"
    await gmail.finalize_code_message("m-code")
    assert gmail.changes[-1] == ("label", "m-code", "id:YahBah/Parsed")


async def test_fetch_keeps_todays_behaviour(llm, no_wait, code_action, logs):
    gmail = FakeGmail([CONFIRMATION, CODE])

    code = await gmail.fetch_verification_code("me+acme1@example.com", since_epoch=int(time.time()) - 30,
                                               timeout_seconds=7, poll_interval=7)

    assert code == "739201"
    assert gmail.changes == [("modify", "m-code", ["id:YahBah/Parsed", "id:YahBah"], ["INBOX"])]
    assert not any("739201" in line for line in logs)


async def test_excluded_message_is_skipped(llm, no_wait):
    gmail = FakeGmail([CONFIRMATION, CODE])

    with pytest.raises(RuntimeError, match="No verification code"):
        await gmail.wait_for_verification_code("me+acme1@example.com", since_epoch=int(time.time()) - 30,
                                               timeout_seconds=14, poll_interval=7,
                                               exclude_message_ids={"m-code"})
    assert gmail.changes == []


async def test_messages_from_before_the_submit_are_ignored(llm, no_wait):
    old_code = message("m-old", subject="Your verification code", html="<p>Your code is 111111</p>",
                       age_seconds=3600)
    gmail = FakeGmail([old_code, CODE])

    found = await gmail.wait_for_verification_code("me+acme1@example.com", since_epoch=int(time.time()) - 30,
                                                   timeout_seconds=7, poll_interval=7)

    assert found.message_id == "m-code"


async def test_board_subject_goes_into_the_gmail_query(llm, no_wait):
    gmail = FakeGmail([message("m1", subject="Security code for your application to Acme",
                               html="<p>Paste this code into the security code field:</p><h1>UNFAnJfW</h1>")])

    found = await gmail.wait_for_verification_code("me+acme1@example.com", subject_hint="security code",
                                                   code_pattern=GREENHOUSE_SHAPE, timeout_seconds=7, poll_interval=7)

    assert found.code == "UNFAnJfW"
    assert "subject:(security code)" in gmail.queries[0]
