"""Disposable Postgres for tests (marker `db`).

One database per test session, `yahbah_test_<hex>`, created through TEST_DATABASE_URL (default
postgresql+psycopg://yahbah_test@localhost/postgres; that role has CREATEDB and trust auth),
migrated with `alembic upgrade head` in a subprocess, and dropped at session end. Only names
starting with `yahbah_test_` are ever created, migrated, truncated or dropped.

After migrating, the harness adds:
- harness_status_log, filled by a trigger on every application_run status write. Tests read
  transitions from it (never FAILED? peak RUNNING?) instead of polling, so short-lived states
  can't be missed.
- the KNOWN_DRIFT columns: ORM columns no migration creates (recorded in TestDatabase.drift first).

TestDB (the `db` fixture) is per test: tables truncated first, every `AsyncSessionLocal` in yahbah
pointed at this database through a NullPool engine (no connections shared across event loops).
"""
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from harness.env import REPO

DEFAULT_ADMIN_URL = "postgresql+psycopg://yahbah_test@localhost/postgres"
PREFIX = "yahbah_test_"

STATUS_LOG_SQL = """
CREATE TABLE harness_status_log (
    seq bigserial PRIMARY KEY,
    run_id uuid NOT NULL,
    old_status text,
    new_status text,
    at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION harness_log_status() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO harness_status_log (run_id, old_status, new_status) VALUES (NEW.id, NULL, NEW.status);
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO harness_status_log (run_id, old_status, new_status) VALUES (NEW.id, OLD.status, NEW.status);
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER harness_status_log AFTER INSERT OR UPDATE ON application_run
    FOR EACH ROW EXECUTE FUNCTION harness_log_status();
"""


# ORM columns no migration creates (found 2026-10-19: a fresh `alembic upgrade head` can't load a
# JobPosting). Added after migrating so DB tests can run; test/invariants/task3_run_lifecycle/
# test_schema.py holds the fix. Remove an entry once a migration creates the column.
KNOWN_DRIFT = {("job_posting", "canonical_url"), ("job_posting", "location")}


class PostgresUnavailable(Exception):
    pass


class SchemaNotReady(AssertionError):
    """A test seeded a column the current migrations don't have yet (contract §9)."""


def _libpq(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _checked(url: URL) -> URL:
    if not (url.database or "").startswith(PREFIX):
        raise RuntimeError(f"refusing to touch database {url.database!r}: test databases are named {PREFIX}*")
    return url


@dataclass
class TestDatabase:
    __test__ = False  # not a pytest test class

    url: URL
    admin_url: URL
    drift: set[tuple[str, str]] = field(default_factory=set)  # ORM columns the migrations didn't create

    @property
    def name(self) -> str:
        return self.url.database


def _orm_drift(conn) -> set[tuple[str, str]]:
    from yahbah.db.models import Base

    rows = conn.execute("SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public'").fetchall()
    have = {(t, c) for t, c in rows}
    return {(t.name, c.name) for t in Base.metadata.sorted_tables for c in t.columns if (t.name, c.name) not in have}


def _add_known_drift(conn, drift: set[tuple[str, str]]) -> None:
    from sqlalchemy.dialects import postgresql

    from yahbah.db.models import Base

    for table, column in sorted(drift & KNOWN_DRIFT):
        col_type = Base.metadata.tables[table].columns[column].type.compile(dialect=postgresql.dialect())
        conn.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "{column}" {col_type}')


def _empty_database() -> TestDatabase:
    admin = make_url(os.environ.get("TEST_DATABASE_URL", DEFAULT_ADMIN_URL))
    try:
        conn = psycopg.connect(_libpq(admin), autocommit=True, connect_timeout=3)
    except psycopg.Error as exc:
        reason = (str(exc).strip().splitlines() or ["connection failed"])[0]
        raise PostgresUnavailable(f"{admin.render_as_string(hide_password=True)}: {reason}") from exc
    url = _checked(admin.set(database=f"{PREFIX}{uuid.uuid4().hex[:12]}"))
    with conn:
        conn.execute(f'CREATE DATABASE "{url.database}"')
    return TestDatabase(url=url, admin_url=admin)


def create_database() -> TestDatabase:
    """Migrated to head, plus the harness's status log and the KNOWN_DRIFT columns."""
    db = _empty_database()
    try:
        migrate(db)
        with psycopg.connect(_libpq(db.url), autocommit=True) as c:
            db.drift = _orm_drift(c)
            _add_known_drift(c, db.drift)
            c.execute(STATUS_LOG_SQL)
    except BaseException:
        drop_database(db)
        raise
    return db


def create_database_at(revision: str) -> TestDatabase:
    """Migrated only up to `revision`, nothing added: for testing a later migration's backfill."""
    db = _empty_database()
    try:
        migrate(db, revision)
    except BaseException:
        drop_database(db)
        raise
    return db


def migrate(db: TestDatabase, revision: str = "head") -> None:
    env = {**os.environ, "DATABASE_URL": _checked(db.url).render_as_string(hide_password=False)}
    proc = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=180)
    if proc.returncode:
        raise RuntimeError(f"alembic upgrade {revision} failed on {db.name}:\n{proc.stdout}\n{proc.stderr}")


def execute(db: TestDatabase, sql: str, params: dict | None = None) -> list[dict]:
    """Synchronous SQL on a test database (rows as dicts; [] for statements without rows)."""
    with psycopg.connect(_libpq(_checked(db.url)), autocommit=True) as conn:
        cur = conn.execute(sql, params or {})
        if cur.description is None:
            return []
        names = [d.name for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def drop_database(db: TestDatabase) -> None:
    _checked(db.url)
    with psycopg.connect(_libpq(db.admin_url), autocommit=True, connect_timeout=3) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{db.name}" WITH (FORCE)')


class TestDB:
    """Per-test handle: sessions, seeding, row reads and the status log."""

    __test__ = False  # not a pytest test class

    def __init__(self, database: TestDatabase) -> None:
        self.database = database
        self.engine = create_async_engine(_checked(database.url), poolclass=NullPool)
        self.Session = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self._columns: dict[str, set[str]] = {}

    def session(self) -> AsyncSession:
        return self.Session()

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def truncate(self) -> None:
        async with self.engine.begin() as conn:
            tables = (await conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            ))).scalars().all()
            if tables:
                await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))

    async def execute(self, sql: str, **params):
        async with self.engine.begin() as conn:
            return await conn.execute(text(sql), params)

    async def fetchall(self, sql: str, **params) -> list[dict]:
        async with self.engine.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(text(sql), params)]

    async def fetchone(self, sql: str, **params) -> dict | None:
        rows = await self.fetchall(sql, **params)
        return rows[0] if rows else None

    async def columns(self, table: str) -> set[str]:
        if table not in self._columns:
            rows = await self.fetchall(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = :t",
                t=table)
            self._columns[table] = {r["column_name"] for r in rows}
        return self._columns[table]

    async def insert(self, table: str, **values) -> dict:
        present = await self.columns(table)
        if not present:
            raise SchemaNotReady(f"table {table} does not exist yet (contract §9)")
        if table == "application_run":
            values = self._legacy_run_values(values, present)
        missing = sorted(set(values) - present)
        if missing:
            raise SchemaNotReady(f"{table}.{', '.join(missing)} does not exist yet (contract §9, migration 0018/0019)")
        cols = ", ".join(values)
        params = ", ".join(f":{k}" for k in values)
        result = await self.execute(f"INSERT INTO {table} ({cols}) VALUES ({params}) RETURNING *", **values)
        return dict(result.one()._mapping)

    @staticmethod
    def _legacy_run_values(values: dict, present: set[str]) -> dict:
        """Before migration 0018 a testing-mode run is `is_test_run = true` (the contract's backfill)."""
        if "submission_mode" in values and "submission_mode" not in present and "is_test_run" in present:
            values = dict(values)
            values["is_test_run"] = values.pop("submission_mode") == "testing"
        return values

    async def seed_posting(self, url: str | None = None, **values) -> uuid.UUID:
        url = url or f"https://jobs.harness.example/acme/{uuid.uuid4().hex[:8]}"
        defaults = {"id": uuid.uuid4(), "url": url, "canonical_url": url, "ats_type": "generic",
                    "company_website": "acme.harness.example", "created_at": datetime.now(timezone.utc)}
        return (await self.insert("job_posting", **{**defaults, **values}))["id"]

    async def seed_run(self, posting_id: uuid.UUID | None = None, *, status: str = "PENDING", **values) -> uuid.UUID:
        if posting_id is None:
            posting_id = await self.seed_posting()
        posting = await self.fetchone("SELECT url FROM job_posting WHERE id = :id", id=posting_id)
        run_id = values.pop("id", uuid.uuid4())
        now = datetime.now(timezone.utc)
        defaults = {"id": run_id, "job_posting_id": posting_id, "job_url": posting["url"], "status": status,
                    "temporal_workflow_id": f"application-{run_id}", "created_at": now, "updated_at": now}
        return (await self.insert("application_run", **{**defaults, **values}))["id"]

    async def posting(self, posting_id) -> dict:
        row = await self.fetchone("SELECT * FROM job_posting WHERE id = :id", id=uuid.UUID(str(posting_id)))
        assert row is not None, f"no job_posting {posting_id}"
        return row

    async def run(self, run_id) -> dict:
        row = await self.fetchone("SELECT * FROM application_run WHERE id = :id", id=uuid.UUID(str(run_id)))
        assert row is not None, f"no application_run {run_id}"
        return row

    async def runs(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM application_run ORDER BY created_at")

    async def status_log(self, run_id=None) -> list[tuple]:
        """[(run_id, old_status, new_status)] in write order (one run, or all)."""
        if run_id is None:
            rows = await self.fetchall("SELECT run_id, old_status, new_status FROM harness_status_log ORDER BY seq")
        else:
            rows = await self.fetchall("SELECT run_id, old_status, new_status FROM harness_status_log "
                                       "WHERE run_id = :id ORDER BY seq", id=uuid.UUID(str(run_id)))
        return [(r["run_id"], r["old_status"], r["new_status"]) for r in rows]

    async def statuses_seen(self, run_id) -> list[str]:
        return [new for _, _, new in await self.status_log(run_id)]

    async def peak_running(self) -> int:
        """The most runs that were RUNNING at once (replaying every status write in order)."""
        current: dict = {}
        peak = 0
        for run_id, _, new in await self.status_log():
            current[run_id] = new
            peak = max(peak, sum(1 for s in current.values() if s == "RUNNING"))
        return peak
