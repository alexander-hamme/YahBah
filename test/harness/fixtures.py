"""pytest fixtures of the shared harness, re-exported by test/conftest.py.

  fixture_server    (session)  local HTTP server for test/yahbah/fixtures/ (harness.server)
  pages             (test)     a per-test page directory on that server: pages.write(name, html) -> url
  browser           (module)   headless browser-use session that can only reach localhost [browser]
  llm               (test)     ScriptedLLM installed on OllamaClient (harness.llm)
  agent_llm         (test)     AgentScript installed as every browser-use chat model (harness.agent)
  applicant_facts   (test)     the synthetic applicant's stored answers; .set({...}) rewrites them
  runtime_settings  (test)     config/settings.yaml redirected to a temp file; .set(key=value)
  pg_database       (session)  the disposable yahbah_test_<hex> database; skips if Postgres is down
  db                (test)     TestDB on it, tables truncated, AsyncSessionLocal patched [db]
  temporal_env      (module)   Temporal dev server (harness.temporal) [temporal]
  workflows         (test)     stubs + the real worker + run helpers [temporal, db]
  activity_env      (test)     activity_env(workflow_id=..., attempt=...) -> temporalio ActivityEnvironment
"""
import dataclasses
import importlib
import inspect
import sys
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker

from harness import db as dbh
from harness import env, profile
from harness.agent import AgentScript
from harness.browser import start_session
from harness.llm import ScriptedLLM
from harness.server import FixtureServer

__all__ = ["fixture_server", "pages", "browser", "llm", "agent_llm", "applicant_facts", "runtime_settings",
           "pg_database", "db", "temporal_env", "workflows", "activity_env",
           "_fresh_server_events", "_live_llm_gate"]  # (autouse fixtures: `import *` would skip _names)

# Modules that bind AsyncSessionLocal at import; imported before patching so all of them get patched.
_SESSION_USERS = ("yahbah.workflows.activities.db_ops", "yahbah.workflows.activities.browser",
                  "yahbah.workflows.activities.llm", "yahbah.jobs", "yahbah.llm.field_mapper",
                  "yahbah.worker", "yahbah.api.routes.runs", "yahbah.api.routes.jobs")


def _passed(request) -> bool:
    report = getattr(request.node, "harness_call_report", None)
    return report is not None and report.passed


# ── pages and browser ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def fixture_server():
    server = FixtureServer().start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _fresh_server_events(request):
    """Each test sees only the page events it caused."""
    if "fixture_server" in request.fixturenames:
        request.getfixturevalue("fixture_server").clear_events()


class Pages:
    def __init__(self, server: FixtureServer, directory: Path, prefix: str) -> None:
        self.server, self.dir, self.prefix = server, directory, prefix
        self.base = server.mount(prefix, directory)

    def write(self, name: str, html: str) -> str:
        (self.dir / name).write_text(html)
        return self.url(name)

    def url(self, name: str) -> str:
        return self.base + name


@pytest.fixture
def pages(fixture_server, tmp_path):
    prefix = f"t/{uuid.uuid4().hex[:8]}"
    yield Pages(fixture_server, tmp_path, prefix)
    fixture_server.unmount(prefix)


@pytest.fixture(scope="module")
async def browser():
    session = await start_session()
    yield session
    await session.kill()


# ── models ────────────────────────────────────────────────────────────────────

@pytest.fixture
def llm(request, monkeypatch):
    scripted = ScriptedLLM().install(monkeypatch)
    yield scripted
    if scripted.unexpected and _passed(request):
        pytest.fail(f"unexpected model calls (swallowed by the code): "
                    f"{[(c.role, c.schema_name) for c in scripted.unexpected]}")


@pytest.fixture
def agent_llm(request, monkeypatch):
    script = AgentScript().install(monkeypatch)
    if "browser" in request.fixturenames:
        script.session = request.getfixturevalue("browser")
    return script


@pytest.fixture(autouse=True)
def _live_llm_gate(request):
    """Real models only for tests marked live_llm (opt-in, see conftest)."""
    live = request.node.get_closest_marker("live_llm") is not None
    env.allow_ollama(live)
    yield
    env.allow_ollama(False)


# ── configuration ─────────────────────────────────────────────────────────────

class ApplicantFacts:
    """The synthetic applicant's stored answers (config/personal.yaml known_answers)."""

    def __init__(self, path: Path, monkeypatch) -> None:
        from yahbah.config import load_prompts_config, settings

        self.path, self._clear = path, load_prompts_config.cache_clear
        monkeypatch.setattr(settings, "personal_path", str(path))
        self.set(profile.KNOWN_ANSWERS)

    def set(self, known_answers: dict[str, str]) -> None:
        self.known_answers = dict(known_answers)
        profile.write_personal(self.path, self.known_answers)
        self._clear()

    def update(self, **answers: str) -> None:
        self.set({**self.known_answers, **answers})


@pytest.fixture
def applicant_facts(tmp_path, monkeypatch):
    facts = ApplicantFacts(tmp_path / "personal.yaml", monkeypatch)
    yield facts
    facts._clear()  # monkeypatch restores the session's synthetic file; drop the cached copy


class RuntimeSettings:
    """config/settings.yaml for the test. `testing_mode` (removed by contract §3) is written alongside
    submission_mode until task 3 lands, so pre-contract readers see the same mode."""

    DEFAULTS = {"submission_mode": "testing", "review_window_hours": 48,
                "job_alert_polling_enabled": False, "gmail_status_polling_enabled": False}

    def __init__(self, path: Path) -> None:
        self.path = path
        self.values: dict = {}
        self.set(**self.DEFAULTS)

    def set(self, **values) -> None:
        self.values.update(values)
        self._write()

    def unset(self, *keys: str) -> None:
        for key in keys:
            self.values.pop(key, None)
        self._write()

    def _write(self) -> None:
        data = dict(self.values)
        data.setdefault("testing_mode", data.get("submission_mode") == "testing")
        self.path.write_text(yaml.safe_dump(data, sort_keys=False))


@pytest.fixture
def runtime_settings(tmp_path, monkeypatch):
    import yahbah.config as config

    path = tmp_path / "settings.yaml"
    monkeypatch.setattr(config, "_SETTINGS_PATH", path)
    return RuntimeSettings(path)


# ── database ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def pg_database():
    try:
        database = dbh.create_database()
    except dbh.PostgresUnavailable as exc:
        pytest.skip(f"Postgres unreachable ({exc}); set TEST_DATABASE_URL")
    yield database
    dbh.drop_database(database)


def point_sessions_at(test_db: dbh.TestDB, monkeypatch) -> None:
    """Every yahbah module's AsyncSessionLocal (and the session module's engine) -> the test DB."""
    import yahbah.db.session as session_module

    for name in _SESSION_USERS:
        importlib.import_module(name)
    for module in list(sys.modules.values()):
        if (getattr(module, "__name__", "").startswith("yahbah")
                and isinstance(getattr(module, "AsyncSessionLocal", None), async_sessionmaker)):
            monkeypatch.setattr(module, "AsyncSessionLocal", test_db.Session)
    monkeypatch.setattr(session_module, "engine", test_db.engine)


@pytest.fixture
async def db(pg_database, monkeypatch):
    test_db = dbh.TestDB(pg_database)
    await test_db.truncate()
    point_sessions_at(test_db, monkeypatch)
    yield test_db
    await test_db.dispose()


# ── temporal ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
async def temporal_env():
    from harness.temporal import start_env

    environment = await start_env()
    yield environment
    await environment.shutdown()


@pytest.fixture
async def workflows(temporal_env, db, runtime_settings, monkeypatch):
    from harness.temporal import Stubs, WorkerHarness, Workflows

    task_queue = f"harness-{uuid.uuid4().hex[:8]}"
    stubs = Stubs(runtime_settings)
    worker = WorkerHarness(temporal_env, stubs, task_queue, monkeypatch)
    wf = Workflows(temporal_env, db, stubs, worker, task_queue)
    await worker.start()
    yield wf
    await wf.cleanup()


@pytest.fixture
def activity_env():
    """activity_env(workflow_id=..., attempt=...) -> an ActivityEnvironment whose `call(fn, *args)` runs
    fn (sync or async) inside an activity context with that info."""
    from temporalio.testing import ActivityEnvironment

    class Environment(ActivityEnvironment):
        async def call(self, fn, *args):
            result = self.run(fn, *args)
            return await result if inspect.isawaitable(result) else result

    def make(workflow_id: str = "application-harness", attempt: int = 1, **info):
        environment = Environment()
        environment.info = dataclasses.replace(environment.info, workflow_id=workflow_id, attempt=attempt, **info)
        return environment

    return make
