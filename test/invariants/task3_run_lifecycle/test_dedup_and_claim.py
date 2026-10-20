"""possibly_applied(), the submission claim and the dedup sites (contract §5, §6; audit Temporal F2,
Forms F2, QA F2)."""
from datetime import datetime, timezone

import pytest

from harness.api import FakeTemporal, api_client
from harness.contract import contract_symbol, defect, make

NOW = datetime.now(timezone.utc)

# case -> (seeded run, the Blocker.reason values the contract allows for it)
BLOCKING = {
    "failed_after_submit_attempt": (dict(status="FAILED", submit_attempted_at=NOW), {"attempted"}),
    "pending": (dict(status="PENDING"), {"in_flight"}),
    "running": (dict(status="RUNNING"), {"in_flight"}),
    "awaiting_review": (dict(status="AWAITING_REVIEW"), {"in_flight"}),
    "completed": (dict(status="COMPLETED", submission_mode="review"), {"completed"}),
    "completed_legacy": (dict(status="COMPLETED"), {"completed"}),  # submission_mode NULL: before the contract
    # (§5 doesn't rank reasons when several hold)
    "submit_uncertain": (dict(status="SUBMIT_UNCERTAIN", submit_attempted_at=NOW), {"submit_uncertain", "attempted"}),
    "email_confirmed": (dict(status="FAILED", email_confirmed_at=NOW), {"email_confirmed"}),
}
NOT_BLOCKING = {
    "testing_mode_completed": dict(status="COMPLETED", submission_mode="testing"),
    "failed_before_submit": dict(status="FAILED"),
    "cancelled": dict(status="CANCELLED"),
    "expired": dict(status="EXPIRED"),
    "duplicate": dict(status="DUPLICATE"),
}


async def _possibly_applied(db, posting_id, **kwargs):
    possibly_applied = contract_symbol("possibly_applied")
    async with db.session() as session:
        return await possibly_applied(session, posting_id, **kwargs)


@defect("contract §5", 3, "possibly_applied() doesn't exist; dedup sites count only COMPLETED (or non-FAILED) runs")
@pytest.mark.parametrize("case", BLOCKING)
async def test_possibly_applied_blocks(db, case) -> None:
    contract_symbol("possibly_applied")
    seed, reasons = BLOCKING[case]
    posting = await db.seed_posting()
    blocker = await db.seed_run(posting, **seed)
    found = await _possibly_applied(db, posting)
    assert found is not None
    assert (found.run_id, found.status) == (blocker, seed["status"]) and found.reason in reasons
    assert await _possibly_applied(db, posting, exclude_run_id=blocker) is None


@defect("contract §5", 3, "possibly_applied() doesn't exist; test runs count as applied today")
@pytest.mark.parametrize("case", NOT_BLOCKING)
async def test_possibly_applied_does_not_block(db, case) -> None:
    contract_symbol("possibly_applied")
    posting = await db.seed_posting()
    await db.seed_run(posting, **NOT_BLOCKING[case])
    assert await _possibly_applied(db, posting) is None


@defect("contract §5, §6", 3, "possibly_applied() doesn't exist; no submission_claim table")
async def test_a_submission_claim_blocks_a_run_that_would_not(db) -> None:
    contract_symbol("possibly_applied")
    posting = await db.seed_posting()
    cancelled = await db.seed_run(posting, status="CANCELLED")
    await db.insert("submission_claim", posting_key=str(posting), run_id=cancelled, claimed_at=NOW)
    found = await _possibly_applied(db, posting)
    assert found is not None and (found.run_id, found.reason) == (cancelled, "claim")


@defect("contract §6", 3, "claims don't exist, so deleting a run removes its duplicate protection")
async def test_a_claim_outlives_its_deleted_run(db) -> None:
    contract_symbol("possibly_applied")
    posting = await db.seed_posting()
    run_id = await db.seed_run(posting, status="SUBMIT_UNCERTAIN", submit_attempted_at=NOW)
    await db.insert("submission_claim", posting_key=str(posting), run_id=run_id, claimed_at=NOW)
    await db.execute("DELETE FROM application_run WHERE id = :id", id=run_id)
    found = await _possibly_applied(db, posting)
    assert found is not None, "a claim without its run must still block"
    assert (found.run_id, found.reason) == (None, "claim")


@defect("contract §6", 3, "claim_submission() doesn't exist; mark_submit_attempted is a blind UPDATE")
async def test_a_second_claim_for_the_same_posting_is_denied(db) -> None:
    claim_submission = contract_symbol("claim_submission")
    posting = await db.seed_posting()
    first = await db.seed_run(posting, status="RUNNING")
    assert await claim_submission(str(first), f"application-{first}", posting) is True
    await db.execute("UPDATE application_run SET status = 'SUBMIT_UNCERTAIN' WHERE id = :id", id=first)

    second = await db.seed_run(posting, status="RUNNING")  # slipped past dedup somehow
    assert await claim_submission(str(second), f"application-{second}", posting) is False
    assert (await db.run(second))["submit_attempted_at"] is None  # rolled back


@defect("contract §6", 3, "claim_submission() doesn't exist; any executor can mark the run attempted")
async def test_a_claim_with_the_wrong_workflow_id_is_denied(db) -> None:
    claim_submission = contract_symbol("claim_submission")
    posting = await db.seed_posting()
    run_id = await db.seed_run(posting, status="RUNNING")
    assert await claim_submission(str(run_id), "application-some-other-workflow", posting) is False
    assert (await db.run(run_id))["submit_attempted_at"] is None
    assert await db.fetchall("SELECT * FROM submission_claim") == []


async def _check_duplicate(db, run_id, posting_id):
    from yahbah.schemas import DuplicateCheckInput
    from yahbah.workflows.activities.db_ops import check_duplicate_activity

    url = (await db.posting(posting_id))["url"]
    return await check_duplicate_activity(make(DuplicateCheckInput, run_id=str(run_id), canonical_url=url,
                                               job_title=None, job_company=None, job_location=None))


@defect("contract §5", 3, "check_duplicate_activity counts only COMPLETED runs: a FAILED run that clicked submit is invisible")
async def test_check_duplicate_sees_a_run_that_attempted_submit(db) -> None:
    posting = await db.seed_posting()
    await db.seed_run(posting, status="FAILED", submit_attempted_at=NOW, completed_at=NOW)
    current = await db.seed_run(posting, status="RUNNING")
    assert (await _check_duplicate(db, current, posting)).is_duplicate


@defect("contract §5", 3, "a testing-mode COMPLETED run counts as applied and blocks the real application")
async def test_check_duplicate_ignores_testing_mode_runs(db) -> None:
    posting = await db.seed_posting()
    await db.seed_run(posting, status="COMPLETED", submission_mode="testing", completed_at=NOW)
    current = await db.seed_run(posting, status="RUNNING")
    assert not (await _check_duplicate(db, current, posting)).is_duplicate


@defect("contract §5", 3, "POST /jobs has no dedup: a posting whose submit was attempted is enqueued again")
async def test_post_jobs_refuses_a_possibly_applied_posting(db, runtime_settings, monkeypatch) -> None:
    import yahbah.api.routes.jobs as jobs_route

    async def unchanged(url: str) -> str:
        return url

    monkeypatch.setattr(jobs_route, "normalize_job_url", unchanged)  # redirect-following fetch: offline here
    url = "https://jobs.harness.example/acme/123"
    posting = await db.seed_posting(url)
    attempted = await db.seed_run(posting, status="FAILED", submit_attempted_at=NOW)
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post("/jobs", json={"job_url": url})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert (detail["code"], detail.get("run_id")) == ("already_applied", str(attempted))  # §8 error body
    assert temporal.started == []
