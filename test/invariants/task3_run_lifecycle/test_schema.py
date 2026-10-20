"""The migration chain reproduces the ORM schema (found by the harness 2026-10-19)."""
from harness.contract import defect


@defect("migrations (contract §9 area)", 3, "job_posting.canonical_url and .location are in the ORM but no "
                                            "migration creates them: a fresh `alembic upgrade head` can't load a JobPosting")
def test_migrations_create_every_orm_column(pg_database) -> None:
    assert pg_database.drift == set(), f"ORM columns missing after alembic upgrade head: {sorted(pg_database.drift)}"
