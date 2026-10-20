"""answer_cache on the disposable test database: provenance, reuse rules, namespaces, migration 0019."""
import os
import subprocess
import sys

import pytest

from harness import db as dbh
from harness.env import REPO
from harness.llm import answer, map_all
from harness.profile import applicant
from yahbah.llm import answer_cache
from yahbah.llm.field_mapper import FieldMapper
from yahbah.llm.models import spec
from yahbah.schemas import FormField

QUESTION = FormField(key="1", label="Do you have experience with PostgreSQL?", field_type="radio", required=True,
                     options=["Yes", "No"])


@pytest.fixture
def prose(llm, applicant_facts):
    llm.on("driver", "MappingResponse", handler=map_all("custom"))
    llm.on("prose", handler=lambda call: answer("Yes", basis=["skills"]))
    return llm


async def rows(db) -> list[dict]:
    return await db.fetchall("SELECT * FROM answer_cache ORDER BY namespace")


async def test_a_grounded_fact_is_cached_with_its_provenance_and_reused(db, prose) -> None:
    first = await FieldMapper().map([QUESTION], applicant())
    [row] = await rows(db)
    assert (row["namespace"], row["question"], row["answer"], row["basis"]) == ("live", QUESTION.label, "Yes", ["skills"])
    assert row["model"] == spec("prose").model and row["prompt_hash"] and row["basis_hash"]
    assert row["run_id"] is None  # outside a run (a debug or unit call)

    again = await FieldMapper().map([QUESTION], applicant())
    assert [(m.value, m.basis) for m in again] == [(m.value, m.basis) for m in first] == [("Yes", ["skills"])]
    assert len(prose.calls_for("prose")) == 1
    assert (await rows(db))[0]["hits"] == 1


async def test_debug_runs_keep_their_own_namespace(db, prose) -> None:
    await FieldMapper(cache_namespace=answer_cache.DEBUG).map([QUESTION], applicant())
    await FieldMapper().map([QUESTION], applicant())
    assert [r["namespace"] for r in await rows(db)] == ["debug", "live"]
    assert len(prose.calls_for("prose")) == 2  # the debug row never served the live run


async def test_a_changed_basis_value_or_stored_key_set_makes_the_row_stale(db, prose, applicant_facts) -> None:
    await FieldMapper().map([QUESTION], applicant())
    hashes = [(await rows(db))[0]["basis_hash"]]
    await FieldMapper().map([QUESTION], applicant(skills=["Python", "MySQL"]))  # the cited value changed
    hashes.append((await rows(db))[0]["basis_hash"])
    applicant_facts.update(preferred_database="PostgreSQL")  # a new stored answer might answer it better
    await FieldMapper().map([QUESTION], applicant(skills=["Python", "MySQL"]))
    hashes.append((await rows(db))[0]["basis_hash"])
    assert len(prose.calls_for("prose")) == 3 and len(set(hashes)) == 3  # each miss overwrote the row
    assert len(await rows(db)) == 1


async def test_a_run_is_recorded_as_the_source(db, prose) -> None:
    from yahbah.llm.logs import use_run_log_dir
    run_id = await db.seed_run(status="RUNNING")
    use_run_log_dir(str(run_id))
    await FieldMapper().map([QUESTION], applicant())
    assert (await rows(db))[0]["run_id"] == run_id
    await db.execute("DELETE FROM application_run WHERE id = :id", id=run_id)
    assert (await rows(db))[0]["run_id"] is None  # deleting the run keeps the row (ON DELETE SET NULL)


# ── migration 0019 ────────────────────────────────────────────────────────────

def _alembic(database: dbh.TestDatabase, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": dbh._checked(database.url).render_as_string(hide_password=False)}
    proc = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=REPO, env=env, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, f"alembic {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"


def _columns(database) -> set[str]:
    return {r["column_name"] for r in dbh.execute(
        database, "SELECT column_name FROM information_schema.columns WHERE table_name = 'answer_cache'")}


@pytest.mark.db
def test_migration_0019_upgrades_and_downgrades(pg_database) -> None:
    try:
        database = dbh.create_database_at("0018")
    except dbh.PostgresUnavailable as exc:
        pytest.skip(f"Postgres unreachable ({exc})")
    try:
        dbh.execute(database, "INSERT INTO answer_cache (question_key, question, options, answer) "
                              "VALUES ('k', 'Available from', '[]', 'Immediately')")
        _alembic(database, "upgrade", "0019")
        assert dbh.execute(database, "SELECT * FROM answer_cache") == []  # rows without provenance are gone
        assert {"namespace", "basis", "basis_hash", "model", "prompt_hash", "run_id"} <= _columns(database)
        pk = dbh.execute(database, "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
                                   "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = 'answer_cache'::regclass "
                                   "AND i.indisprimary")
        assert {r["attname"] for r in pk} == {"namespace", "question_key"}
        dbh.execute(database, "INSERT INTO answer_cache (namespace, question_key, question, options, answer, basis, "
                              "basis_hash, model, prompt_hash) VALUES ('debug', 'k', 'q', '[]', 'a', '[]', 'h', "
                              "'m', 'p'), ('live', 'k', 'q', '[]', 'a', '[]', 'h', 'm', 'p')")

        _alembic(database, "downgrade", "0018")
        assert not {"namespace", "basis", "basis_hash"} & _columns(database)
        assert [r["question_key"] for r in dbh.execute(database, "SELECT * FROM answer_cache")] == ["k"]
        _alembic(database, "upgrade", "head")
    finally:
        dbh.drop_database(database)
