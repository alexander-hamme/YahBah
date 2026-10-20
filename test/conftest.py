"""Suite-wide test setup. The shared harness lives in test/harness/ (see its __init__ and
docs/wiki/development.md#tests).

Before any yahbah module is imported, harness.env installs the process guards: no app database,
no network beyond localhost, no Ollama outside live_llm, artifacts and personal data in a temp dir.

Markers (registered in pyproject.toml):
  browser   headless Chrome on local pages         db        the disposable Postgres database
  temporal  a local Temporal dev server            live_llm  real local models: opt-in only
  slow      waits for a real timeout to expire     e2e       C7: whole runs, real worker + Chrome
Tests that use the browser / db / workflows / temporal_env fixtures get the matching marker
automatically, so `-m "not db"` and friends deselect exactly those.
live_llm tests are skipped unless selected with `-m live_llm` or YAHBAH_LIVE_LLM=1.

Runs are parallel (pytest-xdist, `-n auto --dist load` set by pytest_cmdline_main below); `-n 0` or `-p no:xdist` runs
serially, for debugging. Default selection: a bare `uv run pytest` (no paths, no -m) deselects the `e2e`
tests (C7). Naming paths or node ids, giving any -m expression, or passing --complete runs them too.
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


_NOT_BY_DEFAULT = ("e2e",)


@pytest.hookimpl(tryfirst=True)  # before pytest-xdist reads -n
def pytest_cmdline_main(config) -> None:
    """Parallel by default: `-n auto --dist load` unless -n was given. Set here rather than in addopts so that
    `-p no:xdist` (which removes the -n option) still parses; `-n 0` is the other way to run serially."""
    if (config.pluginmanager.hasplugin("xdist") and not hasattr(config, "workerinput")
            and getattr(config.option, "numprocesses", 0) is None):
        config.option.numprocesses = "auto"
        if getattr(config.option, "dist", "no") == "no":
            config.option.dist = "load"


def pytest_addoption(parser) -> None:
    parser.addoption("--complete", action="store_true",
                     help="also run the e2e tests (C7) a bare `pytest` deselects (see test/conftest.py)")


def _live_llm_selected(config) -> bool:
    expr = config.getoption("markexpr") or ""
    return os.environ.get("YAHBAH_LIVE_LLM") == "1" or ("live_llm" in expr and "not live_llm" not in expr)


def _default_selection(config) -> bool:
    """A bare run: no paths or node ids named, no -m, no --complete."""
    return (config.args_source == pytest.Config.ArgsSource.TESTPATHS and not config.getoption("markexpr")
            and not config.getoption("complete"))


def pytest_report_header(config) -> str | None:
    if _default_selection(config):
        return f"default selection: {' and '.join(_NOT_BY_DEFAULT)} tests deselected (--complete runs them)"
    return None


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
    if _default_selection(config):
        deselected = [item for item in items if any(item.get_closest_marker(m) for m in _NOT_BY_DEFAULT)]
        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = [item for item in items if item not in deselected]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when == "call":
        item.harness_call_report = report  # read by fixtures that check for swallowed errors
    return report


def pytest_sessionfinish(session) -> None:
    """Under pytest-xdist, a worker hands what its offline guard blocked to the controller."""
    if hasattr(session.config, "workeroutput"):
        session.config.workeroutput["blocked"] = {"hosts": env.BLOCKED_HOSTS, "connections": env.BLOCKED_CONNECTIONS}


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    blocked = getattr(node, "workeroutput", {}).get("blocked") or {}
    env.BLOCKED_HOSTS.extend(blocked.get("hosts", []))
    env.BLOCKED_CONNECTIONS.extend(blocked.get("connections", []))


def pytest_terminal_summary(terminalreporter) -> None:
    if env.BLOCKED_HOSTS or env.BLOCKED_CONNECTIONS:
        terminalreporter.section("offline guard")
        if env.BLOCKED_HOSTS:
            terminalreporter.line(f"blocked DNS lookups: {sorted(set(env.BLOCKED_HOSTS))}")
        if env.BLOCKED_CONNECTIONS:
            terminalreporter.line(f"blocked connections: {sorted(set(env.BLOCKED_CONNECTIONS))}")
