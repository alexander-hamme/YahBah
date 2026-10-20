"""GET /applications and /applications/stats on the disposable DB (contract §8): rows carry outcome and
actions from run_state, the outcome filter and stats agree with derive_outcome() for every status, mode
and confirmation combination, and the review queue sorts by deadline."""
from datetime import datetime, timedelta, timezone
from typing import get_args

import httpx
import pytest

from yahbah.run_state import Outcome, RunStatus, allowed_actions, derive_outcome

NOW = datetime.now(timezone.utc)
MODES = ["testing", "review", None, "auto_reviewed"]  # auto_reviewed: a mode this server doesn't know yet


def _client() -> httpx.AsyncClient:
    from yahbah.api.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                             base_url="http://localhost")


async def _seed_every_combination(db) -> dict[str, dict]:
    """One run per (status, mode, confirmed); returns the expected outcome and actions by run id."""
    expected = {}
    for status in get_args(RunStatus):
        for mode in MODES:
            for confirmed in (None, NOW):
                attempted = NOW if status in ("SUBMIT_UNCERTAIN", "COMPLETED") and mode != "testing" else None
                run_id = await db.seed_run(status=status, submission_mode=mode, email_confirmed_at=confirmed,
                                           submit_attempted_at=attempted)
                expected[str(run_id)] = {
                    "outcome": derive_outcome(status, mode, confirmed),
                    "actions": allowed_actions(status, mode, attempted),
                }
    return expected


async def _all_rows(client, **params) -> list[dict]:
    rows, page = [], 1
    while True:
        resp = await client.get("/applications", params={**params, "page": page, "per_page": 100})
        assert resp.status_code == 200, resp.text
        rows += resp.json()["items"]
        if len(rows) >= resp.json()["total"]:
            return rows
        page += 1


async def test_rows_carry_outcome_actions_and_lifecycle_fields(db) -> None:
    expected = await _seed_every_combination(db)
    retried = await db.seed_run(status="FAILED", submission_mode="review", origin="discovered",
                                failure_reason="login_wall", review_deadline=NOW, review_round=2,
                                retry_of_run_id=next(iter(expected)))
    async with _client() as client:
        rows = {r["run_id"]: r for r in await _all_rows(client)}

    for run_id, want in expected.items():
        assert (rows[run_id]["outcome"], rows[run_id]["actions"]) == (want["outcome"], want["actions"]), rows[run_id]
    row = rows[str(retried)]
    assert row["origin"] == "discovered" and row["failure_reason"] == "login_wall"
    assert row["retry_of_run_id"] == next(iter(expected)) and row["review_deadline"] is not None
    assert row["review_round"] == 2
    assert {"submission_mode", "submit_attempted_at", "status"} <= set(row)
    assert "is_test_run" not in row


async def test_outcome_filter_matches_derive_outcome_for_every_combination(db) -> None:
    expected = await _seed_every_combination(db)
    async with _client() as client:
        for outcome in get_args(Outcome):
            got = {r["run_id"] for r in await _all_rows(client, outcome=outcome)}
            want = {run_id for run_id, e in expected.items() if e["outcome"] == outcome}
            assert got == want, outcome


async def test_outcome_filter_takes_several_values(db) -> None:
    review = await db.seed_run(status="AWAITING_REVIEW", submission_mode="review", review_deadline=NOW)
    uncertain = await db.seed_run(status="SUBMIT_UNCERTAIN", submission_mode="review", submit_attempted_at=NOW)
    await db.seed_run(status="FAILED", submission_mode="review")
    async with _client() as client:
        repeated = await _all_rows(client, outcome=["awaiting_review", "submit_uncertain"])
        comma = await _all_rows(client, outcome="awaiting_review,submit_uncertain")
        everything = await _all_rows(client)
    assert {r["run_id"] for r in repeated} == {r["run_id"] for r in comma} == {str(review), str(uncertain)}
    assert len(everything) == 3


async def test_an_unknown_outcome_is_rejected_with_the_error_body(db) -> None:
    async with _client() as client:
        resp = await client.get("/applications", params={"outcome": ["failed", "yolo"]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_outcome"
    assert "yolo" in resp.json()["detail"]["message"]


async def test_review_deadline_sort_puts_the_soonest_first_and_undated_last(db) -> None:
    later = await db.seed_run(status="AWAITING_REVIEW", submission_mode="review", review_deadline=NOW + timedelta(hours=40))
    soon = await db.seed_run(status="AWAITING_REVIEW", submission_mode="review", review_deadline=NOW + timedelta(hours=2))
    undated = await db.seed_run(status="FAILED", submission_mode="review")
    async with _client() as client:
        rows = await _all_rows(client, sort="review_deadline_asc")
    assert [r["run_id"] for r in rows] == [str(soon), str(later), str(undated)]


async def test_stats_count_every_outcome(db) -> None:
    expected = await _seed_every_combination(db)
    async with _client() as client:
        resp = await client.get("/applications/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["counts"]) == set(get_args(Outcome))
    for outcome in get_args(Outcome):
        assert body["counts"][outcome] == sum(e["outcome"] == outcome for e in expected.values()), outcome
    assert body["total"] == len(expected)


@pytest.mark.parametrize("outcome", ["awaiting_review", "submit_uncertain"])
async def test_stats_on_an_empty_database_are_zeros(db, outcome) -> None:
    async with _client() as client:
        body = (await client.get("/applications/stats")).json()
    assert body["counts"][outcome] == 0 and body["total"] == 0
