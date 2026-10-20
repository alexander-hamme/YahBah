"""The migration chain reproduces the ORM schema (found by the harness 2026-10-19)."""


def test_migrations_create_every_orm_column(pg_database) -> None:
    assert pg_database.drift == set(), f"ORM columns missing after alembic upgrade head: {sorted(pg_database.drift)}"
