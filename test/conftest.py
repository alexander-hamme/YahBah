"""Suite-wide test setup. The shared harness lives in test/harness/ (see its __init__ and
docs/wiki/development.md#tests).

Before any yahbah module is imported, harness.env installs the process guards: no app database,
no network beyond localhost, no Ollama outside live_llm, artifacts and personal data in a temp dir.

Markers (registered in pyproject.toml):
  browser   headless Chrome on local pages         db        the disposable Postgres database
  temporal  a local Temporal dev server            live_llm  real local models: opt-in only
Tests that use the browser / db / workflows / temporal_env fixtures get the matching marker
automatically, so `-m "not db"` and friends deselect exactly those.
live_llm tests are skipped unless selected with `-m live_llm` or YAHBAH_LIVE_LLM=1.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _register_harness() -> None:
    if "harness" in sys.modules:
        return
    directory = Path(__file__).parent / "harness"
    spec = importlib.util.spec_from_file_location("harness", directory / "__init__.py",
                                                  submodule_search_locations=[str(directory)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["harness"] = module
    spec.loader.exec_module(module)


_register_harness()

from harness import env  # noqa: E402

env.install()

from harness.fixtures import *  # noqa: E402,F401,F403

_AUTO_MARKERS = {"browser": "browser", "db": "db", "pg_database": "db", "workflows": "temporal",
                 "temporal_env": "temporal"}


def _live_llm_selected(config) -> bool:
    expr = config.getoption("markexpr") or ""
    return os.environ.get("YAHBAH_LIVE_LLM") == "1" or ("live_llm" in expr and "not live_llm" not in expr)


@pytest.hookimpl(tryfirst=True)  # before -m deselection reads the markers
def pytest_collection_modifyitems(config, items) -> None:
    live = _live_llm_selected(config)
    skip_live = pytest.mark.skip(reason="live_llm is opt-in: run with -m live_llm or YAHBAH_LIVE_LLM=1")
    for item in items:
        for fixture, marker in _AUTO_MARKERS.items():
            if fixture in getattr(item, "fixturenames", ()) and item.get_closest_marker(marker) is None:
                item.add_marker(marker)
        if item.get_closest_marker("live_llm") and not live:
            item.add_marker(skip_live)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when == "call":
        item.harness_call_report = report  # read by fixtures that check for swallowed errors
    return report


def pytest_terminal_summary(terminalreporter) -> None:
    if env.BLOCKED_HOSTS or env.BLOCKED_CONNECTIONS:
        terminalreporter.section("offline guard")
        if env.BLOCKED_HOSTS:
            terminalreporter.line(f"blocked DNS lookups: {sorted(set(env.BLOCKED_HOSTS))}")
        if env.BLOCKED_CONNECTIONS:
            terminalreporter.line(f"blocked connections: {sorted(set(env.BLOCKED_CONNECTIONS))}")
