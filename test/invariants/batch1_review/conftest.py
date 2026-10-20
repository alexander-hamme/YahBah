"""Fixtures for the batch-1 close-out review invariants (QA, 2026-10-20).

The activity-level tests run the real browser activities (yahbah.workflows.activities.browser) in an
activity context on the test database, with Chrome replaced by FakeBrowserSession and the page-level
code (fill_form, submit_and_verify, discover) replaced per test.
"""
from types import SimpleNamespace

import pytest

from harness.activities import FakeBrowserSession
from harness.forms import CappedAsyncio


@pytest.fixture
def registry(monkeypatch):
    """A fresh SessionRegistry singleton whose sessions are FakeBrowserSessions."""
    from yahbah.agent import session as session_module

    monkeypatch.setattr(session_module, "BrowserSession", FakeBrowserSession)
    monkeypatch.setattr(session_module, "asyncio", CappedAsyncio(0))  # open()'s fixed render wait
    fresh = session_module.SessionRegistry()
    monkeypatch.setattr(session_module.SessionRegistry, "_instance", fresh)
    return fresh


@pytest.fixture
def browser_activities(monkeypatch, registry):
    """yahbah.workflows.activities.browser with the profile and page reads faked (no Chrome)."""
    from yahbah.workflows.activities import browser

    async def profile():
        return SimpleNamespace(resume_path="/tmp/resume.pdf", email="jane@example.test")

    async def no_discover(session, trace):
        return None

    async def form_schema(session):
        return [{"key": "1", "label": "First name"}]

    async def read_values(session, keys):
        return {k: {"value": "Jane"} for k in keys}

    monkeypatch.setattr(browser, "_load_profile", profile)
    monkeypatch.setattr(browser, "discover", no_discover)
    monkeypatch.setattr(browser, "get_form_schema", form_schema)
    monkeypatch.setattr(browser, "read_values", read_values)
    monkeypatch.setattr(browser, "submitted_values", lambda ctx, values: [{"key": k} for k in ctx.fields])
    return browser
