"""Resolving uncertain runs, the actions the API offers, and how HTML evidence is served
(contract §1, §6, §8; roadmap A6). The real /runs routes, a FakeTemporal and the test database."""
import uuid
from datetime import datetime, timezone

import pytest

from harness.api import FakeTemporal, api_client
from harness.contract import defect

NOW = datetime.now(timezone.utc)


async def _uncertain_with_claim(db):
    posting = await db.seed_posting()
    run_id = await db.seed_run(posting, status="SUBMIT_UNCERTAIN", submit_attempted_at=NOW,
                               failure_reason="submit_unverified", completed_at=NOW)
    await db.insert("submission_claim", posting_key=str(posting), run_id=run_id, claimed_at=NOW)
    return posting, run_id


@defect("contract §6", 3, "there is no confirm-submitted route: an uncertain run can't be resolved")
async def test_confirm_submitted_completes_an_uncertain_run(db) -> None:
    _, run_id = await _uncertain_with_claim(db)
    async with api_client(FakeTemporal()) as client:
        resp = await client.post(f"/runs/{run_id}/confirm-submitted")
    assert resp.status_code < 300, resp.text
    assert (await db.run(run_id))["status"] == "COMPLETED"


@defect("contract §6", 3, "there is no confirm-not-submitted route: an uncertain posting can never be applied to again")
async def test_confirm_not_submitted_fails_the_run_and_releases_the_posting(db) -> None:
    posting, run_id = await _uncertain_with_claim(db)
    async with api_client(FakeTemporal()) as client:
        resp = await client.post(f"/runs/{run_id}/confirm-not-submitted")
    assert resp.status_code < 300, resp.text
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"]) == ("FAILED", "submit_unverified")
    assert await db.fetchall("SELECT * FROM submission_claim WHERE posting_key = :k", k=str(posting)) == []


@defect("contract §1, §6", 3, "there are no confirm routes, so nothing fences them on SUBMIT_UNCERTAIN")
@pytest.mark.parametrize("route", ["confirm-submitted", "confirm-not-submitted"])
@pytest.mark.parametrize("status", ["RUNNING", "AWAITING_REVIEW", "FAILED"])
async def test_confirming_a_run_that_is_not_uncertain_changes_nothing(db, route, status) -> None:
    run_id = await db.seed_run(status=status, submit_attempted_at=NOW if status == "RUNNING" else None)
    async with api_client(FakeTemporal()) as client:
        resp = await client.post(f"/runs/{run_id}/{route}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "action_not_allowed"
    assert await db.statuses_seen(run_id) == [status]


# A run's actions decide what the UI offers (§8): retry never for a run that may have been
# submitted, approve/reject only while awaiting review, confirm only while uncertain. The pure
# rules are tested in test/yahbah/test_run_state.py; here the API must carry them.
ACTION_CASES = {
    "submit_uncertain": (dict(status="SUBMIT_UNCERTAIN", submit_attempted_at=NOW),
                         {"confirm_submitted", "confirm_not_submitted"}, {"retry", "approve", "reject", "cancel"}),
    "failed_before_submit": (dict(status="FAILED"), {"retry"},
                             {"confirm_submitted", "confirm_not_submitted", "approve", "reject", "cancel"}),
    "awaiting_review": (dict(status="AWAITING_REVIEW", submission_mode="review", review_round=1),
                        {"approve", "reject"}, {"retry", "confirm_submitted", "confirm_not_submitted"}),
    "running": (dict(status="RUNNING"), {"cancel"}, {"retry", "approve", "reject"}),
    "submitted": (dict(status="COMPLETED", submission_mode="review", submit_attempted_at=NOW), set(),
                  {"retry", "approve", "reject", "cancel", "confirm_submitted", "confirm_not_submitted"}),
    "testing_mode_completed": (dict(status="COMPLETED", submission_mode="testing"), {"retry"}, {"approve", "cancel"}),
}


@defect("contract §8", 3, "run responses carry no `actions`; the UI derives eligibility from status itself")
@pytest.mark.parametrize("case", ACTION_CASES)
async def test_run_detail_offers_only_the_allowed_actions(db, case) -> None:
    seed, offered, withheld = ACTION_CASES[case]
    run_id = await db.seed_run(**seed)
    async with api_client(FakeTemporal()) as client:
        resp = await client.get(f"/runs/{run_id}")
    assert resp.status_code == 200, resp.text
    assert "actions" in resp.json(), "the run response carries no actions"
    actions = set(resp.json()["actions"])
    assert offered <= actions and not (withheld & actions), f"actions {sorted(actions)}"


@defect("contract §8 / A6", 3, "HTML artifacts download as text/html: job-site markup runs in the dashboard's origin")
async def test_html_artifacts_download_as_sandboxed_plain_text(db, tmp_path) -> None:
    run_id = await db.seed_run(status="COMPLETED")
    page = tmp_path / "confirmation.html"
    page.write_text('<p>Thanks</p><img src="x" onerror="fetch(\'/runs\')">')
    artifact = await db.insert("application_artifact", id=uuid.uuid4(), run_id=run_id,
                               artifact_type="confirmation_html", path=str(page), created_at=NOW)
    async with api_client(FakeTemporal()) as client:
        resp = await client.get(f"/runs/{run_id}/artifacts/{artifact['id']}/download")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/plain")
    assert "sandbox" in resp.headers.get("content-security-policy", "")
