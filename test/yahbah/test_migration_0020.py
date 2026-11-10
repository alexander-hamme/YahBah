"""Migration 0020 drops answer_cache; its downgrade restores the table exactly as 0019 left it."""
import os
import subprocess
import sys

import pytest

from harness import db as dbh
from harness.env import REPO


def _alembic(database: dbh.TestDatabase, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": dbh._checked(database.url).render_as_string(hide_password=False)}
    proc = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=REPO, env=env, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, f"alembic {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"


def _exists(database) -> bool:
    return dbh.execute(database, "SELECT to_regclass('public.answer_cache') AS t")[0]["t"] is not None


def _schema(database) -> dict:
    """Columns (order, type, nullability, default), constraints and indexes of answer_cache."""
    return {
        "columns": dbh.execute(database, "SELECT ordinal_position, column_name, data_type, is_nullable, column_default "
                                         "FROM information_schema.columns WHERE table_name = 'answer_cache' "
                                         "ORDER BY ordinal_position"),
        "constraints": dbh.execute(database, "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
                                             "WHERE conrelid = 'answer_cache'::regclass ORDER BY conname"),
        "indexes": dbh.execute(database, "SELECT indexname, indexdef FROM pg_indexes "
                                         "WHERE tablename = 'answer_cache' ORDER BY indexname"),
    }


@pytest.mark.db
def test_migration_0020_drops_answer_cache_and_downgrade_restores_the_0019_table() -> None:
    try:
        database = dbh.create_database_at("0019")
    except dbh.PostgresUnavailable as exc:
        pytest.skip(f"Postgres unreachable ({exc})")
    try:
        at_0019 = _schema(database)
        assert at_0019["columns"], "0019 left no answer_cache table"

        _alembic(database, "upgrade", "head")
        assert not _exists(database)

        _alembic(database, "downgrade", "0019")
        assert _exists(database)
        restored = _schema(database)
        assert restored == at_0019
        columns = {c["column_name"]: c for c in restored["columns"]}
        assert set(columns) == {"namespace", "question_key", "question", "options", "answer", "basis", "basis_hash",
                                "model", "prompt_hash", "run_id", "hits", "created_at", "updated_at"}
        assert columns["namespace"]["column_default"].startswith("'live'")
        assert columns["basis"]["column_default"].startswith("'[]'")
        assert columns["run_id"]["is_nullable"] == "YES"
        defs = {c["conname"]: c["def"] for c in restored["constraints"]}
        assert defs["answer_cache_pkey"] == "PRIMARY KEY (namespace, question_key)"
        assert defs["answer_cache_run_id_fkey"] == (
            "FOREIGN KEY (run_id) REFERENCES application_run(id) ON DELETE SET NULL")

        _alembic(database, "upgrade", "head")
        assert not _exists(database)
    finally:
        dbh.drop_database(database)
