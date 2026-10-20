"""Self-tests of the harness pieces that need no browser, database or Temporal."""
import os
import socket
import urllib.request
from dataclasses import dataclass, field
from typing import Literal

import pytest
from pydantic import BaseModel

from harness import env
from harness.contract import HarnessDrift, fit, make, minimal
from harness.llm import UnexpectedModelCall, answer, map_all, verdict
from yahbah.agent.submit import PostSubmitState
from yahbah.llm.client import LLMError, OllamaClient
from yahbah.llm.field_mapper import _FallbackAnswer, _response_model


# ── guards ────────────────────────────────────────────────────────────────────

def test_the_app_database_is_unreachable_by_default() -> None:
    from yahbah.config import settings
    assert settings.database_url == env.GUARD_DATABASE_URL
    assert "yahbah_test_" in settings.database_url


def test_artifacts_and_personal_data_are_redirected() -> None:
    from yahbah.config import settings
    assert str(env.SESSION_DIR) in settings.artifacts_dir
    assert str(env.SESSION_DIR) in settings.personal_path
    assert os.environ["BROWSER_USE_VERSION_CHECK"] == "false"


def test_dns_beyond_localhost_is_blocked_and_recorded() -> None:
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("pypi.org", 443)
    assert env.BLOCKED_HOSTS.pop() == "pypi.org"  # (removed again: the end-of-run summary lists real leaks)
    assert socket.getaddrinfo("localhost", 80)


def test_fake_domain_resolves_to_a_public_address_that_cannot_be_reached() -> None:
    (family, _, _, _, (ip, port)), = socket.getaddrinfo("jobs.harness.example", 443)
    assert (ip, port) == (env.FAKE_PUBLIC_IP, 443)
    with pytest.raises(OSError, match="blocked"):
        urllib.request.urlopen("https://jobs.harness.example/acme/1", timeout=2)
    assert env.BLOCKED_CONNECTIONS.pop() == str((env.FAKE_PUBLIC_IP, 443))


async def test_ollama_is_blocked_outside_live_llm() -> None:
    with pytest.raises(env.OllamaBlocked):
        await OllamaClient("driver").generate_text("system", "user")


# ── fixture server ────────────────────────────────────────────────────────────

def test_fixture_server_serves_fixtures_pages_and_events(fixture_server, pages) -> None:
    body = urllib.request.urlopen(fixture_server.url("synthetic/mock_ats.html"), timeout=2).read()
    assert b"Submit application" in body
    url = pages.write("x.html", "<p>per-test page</p>")
    assert urllib.request.urlopen(url, timeout=2).read() == b"<p>per-test page</p>"
    urllib.request.urlopen(fixture_server.url("__event?name=submit&n=1"), timeout=2)
    assert [(e.name, e.params) for e in fixture_server.events_named("submit")] == [("submit", {"n": "1"})]
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(pages.base + "../../conftest.py", timeout=2)


# ── scripted models ───────────────────────────────────────────────────────────

async def test_scripted_llm_replies_in_order_and_records_calls(llm) -> None:
    llm.on("driver", "PostSubmitState", replies=[verdict("needs_fix", ["x"]), verdict("submitted")])
    llm.on("prose", handler=lambda call: "free text")
    first = await OllamaClient("driver").generate_structured("s", "u1", PostSubmitState)
    second = await OllamaClient("driver").generate_structured("s", "u2", PostSubmitState)
    assert (first.state, second.state) == ("needs_fix", "submitted")
    assert await OllamaClient("prose").generate_text("s", "u") == "free text"
    assert [c.user for c in llm.calls_for("driver")] == ["u1", "u2"]
    with pytest.raises(UnexpectedModelCall):  # replies used up
        await OllamaClient("driver").generate_structured("s", "u3", PostSubmitState)
    llm.unexpected.clear()  # (the fixture would fail this test otherwise)


async def test_scripted_llm_can_raise_model_errors(llm) -> None:
    llm.on("vision", reply=LLMError("timed out"))
    with pytest.raises(LLMError):
        await OllamaClient("vision").generate_structured("s", "u", PostSubmitState)


def test_answer_fits_todays_fallback_schema() -> None:
    got = fit(_FallbackAnswer, answer("Yes", quotes=["q"], basis=["work_authorization"]))
    assert (got.answer, got.evidence, got.job_specific) == ("Yes", ["q"], False)


def test_fit_filters_enum_lists_and_reports_unknown_required_fields() -> None:
    class Reply(BaseModel):
        answer: str
        basis: list[Literal["work_authorization", "sponsorship_required"]] = []

    got = fit(Reply, answer("No", basis=["sponsorship_required", "job_description"]))
    assert got.basis == ["sponsorship_required"]

    class Drifted(BaseModel):
        answer: str
        jurisdiction: str

    with pytest.raises(HarnessDrift, match="jurisdiction"):
        fit(Drifted, answer("No"))


def test_map_all_builds_a_valid_mapping_reply() -> None:
    from harness.llm import ModelCall
    schema = _response_model(["1", "2"], ["custom", "skip"])
    reply = fit(schema, map_all("custom")(ModelCall("driver", "structured", "", "", schema, 0)))
    assert [(m.key, m.source) for m in reply.mappings] == [("1", "custom"), ("2", "custom")]


def test_make_drops_unknown_fields_and_fills_required_ones() -> None:
    @dataclass
    class Output:
        page_url: str
        fields: list
        board: str = "generic"
        extra: dict = field(default_factory=dict)

    out = make(Output, page_url="u", not_a_field=1)
    assert (out.page_url, out.fields, out.board) == ("u", [], "generic")


def test_minimal_prefers_benign_flags() -> None:
    from yahbah.agent.fill import FixOutcome
    assert minimal(FixOutcome).model_dump() == {"fixed": True, "unresolved": []}
