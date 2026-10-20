"""The /runs and /jobs routes of the run lifecycle (contract §4, §6, §7, §8) on the test database,
with a FakeTemporal that records what the routes ask of Temporal."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from temporalio.service import RPCError, RPCStatusCode

from harness.api import FakeHandle, FakeTemporal, api_client

NOW = datetime.now(timezone.utc)


class GoneTemporal(FakeTemporal):
    """Every workflow is gone (e.g. terminated from the Temporal UI, or a lost dev server)."""

    def get_workflow_handle(self, workflow_id: str, **kwargs):
        temporal = self

        class Handle(FakeHandle):
            async def cancel(self) -> None:
                temporal.actions.append(("cancel", self.id))
                raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")

            async def signal(self, *args, **kwargs) -> None:
                raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")

        return Handle(self, workflow_id)


class FailingTemporal(FakeTemporal):
    async def start_workflow(self, *args, **kwargs):
        raise RuntimeError("Temporal is down")


@pytest.fixture
def offline_urls(monkeypatch):
    import yahbah.api.routes.jobs as jobs_route

    async def unchanged(url: str) -> str:
        return url

    monkeypatch.setattr(jobs_route, "normalize_job_url", unchanged)  # redirect-following fetch: offline here


def artifact_file(tmp_path, name: str, content) -> str:
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return str(path)


# ── run detail and review payload ─────────────────────────────────────────────

async def test_run_detail_carries_outcome_actions_and_the_lifecycle_fields(db) -> None:
    original = await db.seed_run(status="FAILED", failure_reason="form_not_found")
    deadline = NOW + timedelta(hours=48)
    run_id = await db.seed_run(status="AWAITING_REVIEW", submission_mode="review", origin="discovered",
                               review_round=2, review_deadline=deadline, retry_of_run_id=original)
    async with api_client(FakeTemporal()) as client:
        body = (await client.get(f"/runs/{run_id}")).json()
    assert (body["outcome"], body["actions"]) == ("awaiting_review", ["approve", "reject", "cancel"])
    assert (body["submission_mode"], body["origin"], body["review_round"], body["retry_of_run_id"]) == (
        "review", "discovered", 2, str(original))
    assert datetime.fromisoformat(body["review_deadline"]) == deadline
    assert "is_test_run" not in body


async def test_the_review_payload_is_the_current_rounds_evidence(db, tmp_path) -> None:
    posting = await db.seed_posting(title="Engineer", company="Acme")
    run_id = await db.seed_run(posting, status="AWAITING_REVIEW", submission_mode="review", review_round=2,
                               review_deadline=NOW + timedelta(hours=1))
    round1 = [{"key": "1", "label": "First name", "answer": "Jane", "source": "first_name"}]
    round2 = round1 + [{"key": "2", "label": "Cover letter", "answer": "cover_letter.pdf", "source": "cover_letter"}]
    new = [{"key": "9", "label": "Visa type", "answer": "H-1B", "source": "custom", "written": None}]

    async def artifact(kind, path, metadata):
        return (await db.insert("application_artifact", id=uuid.uuid4(), run_id=run_id, artifact_type=kind,
                                path=path, artifact_metadata=json.dumps(metadata),
                                created_at=datetime.now(timezone.utc)))["id"]

    await artifact("cover_letter", artifact_file(tmp_path, "cl.pdf", "pdf"), {"text": "Dear Acme"})
    await artifact("submitted_values", artifact_file(tmp_path, "sv1.json", round1), {"review_round": 1})
    await artifact("screenshot", artifact_file(tmp_path, "s1.png", "png"), {"step": "filled_1", "review_round": 1})
    await artifact("submitted_values", artifact_file(tmp_path, "sv2.json", round2), {"review_round": 2})
    shot = await artifact("screenshot", artifact_file(tmp_path, "s2.png", "png"), {"step": "filled_2", "review_round": 2})
    await artifact("new_fields", artifact_file(tmp_path, "nf2.json", new), {"review_round": 2})

    async with api_client(FakeTemporal()) as client:
        resp = await client.get(f"/runs/{run_id}/review")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["review_round"], body["answers"], body["new_fields"]) == (2, round2, new)
    assert body["screenshot_artifact_id"] == str(shot)
    assert body["cover_letter"]["text"] == "Dear Acme"
    assert body["job"] == {"title": "Engineer", "company": "Acme", "url": (await db.run(run_id))["job_url"]}


async def test_a_run_that_was_never_in_review_has_no_review(db) -> None:
    run_id = await db.seed_run(status="COMPLETED", submission_mode="testing")
    async with api_client(FakeTemporal()) as client:
        resp = await client.get(f"/runs/{run_id}/review")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "not_awaiting_review"


@pytest.mark.parametrize("route", ["approve", "reject"])
async def test_review_decisions_need_a_run_awaiting_review(db, route) -> None:
    run_id = await db.seed_run(status="RUNNING", submission_mode="review", review_round=1)
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post(f"/runs/{run_id}/{route}", json={"review_round": 1})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "not_awaiting_review"
    assert temporal.actions == []


# ── cancel ────────────────────────────────────────────────────────────────────

async def test_cancel_asks_the_workflow_and_writes_nothing(db) -> None:
    run_id = await db.seed_run(status="RUNNING")
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post(f"/runs/{run_id}/cancel")
    assert resp.status_code == 202, resp.text
    assert temporal.actions == [("cancel", f"application-{run_id}")]
    assert await db.statuses_seen(run_id) == ["RUNNING"]


@pytest.mark.parametrize("attempted, status", [(None, "CANCELLED"), (NOW, "SUBMIT_UNCERTAIN")])
async def test_cancel_reconciles_a_run_whose_workflow_is_gone(db, attempted, status) -> None:
    run_id = await db.seed_run(status="RUNNING", submit_attempted_at=attempted)
    async with api_client(GoneTemporal()) as client:
        resp = await client.post(f"/runs/{run_id}/cancel")
    assert resp.status_code < 300, resp.text
    row = await db.run(run_id)
    assert (row["status"], row["failure_reason"]) == (status, "cancelled")


async def test_a_review_decision_for_a_gone_workflow_reconciles_the_run(db) -> None:
    run_id = await db.seed_run(status="AWAITING_REVIEW", submission_mode="review", review_round=1)
    async with api_client(GoneTemporal()) as client:
        resp = await client.post(f"/runs/{run_id}/approve", json={"review_round": 1})
    assert resp.status_code == 409, resp.text
    assert (await db.run(run_id))["status"] == "CANCELLED"


async def test_cancelling_an_ended_run_is_refused(db) -> None:
    run_id = await db.seed_run(status="COMPLETED", submission_mode="review")
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post(f"/runs/{run_id}/cancel")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "action_not_allowed"
    assert temporal.actions == []


# ── retry and enqueue ─────────────────────────────────────────────────────────

async def test_retrying_a_testing_run_creates_a_linked_run_that_inherits_the_origin(db, runtime_settings) -> None:
    runtime_settings.set(submission_mode="review")
    original = await db.seed_run(status="COMPLETED", submission_mode="testing", origin="discovered")
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post(f"/runs/{original}/retry")
    assert resp.status_code == 200, resp.text
    new = await db.run(resp.json()["run_id"])
    assert (new["retry_of_run_id"], new["origin"], new["submission_mode"]) == (original, "discovered", "review")
    assert temporal.actions == [("terminate", f"application-{original}")]
    assert temporal.started[0]["arg"].origin == "discovered"


async def test_retry_while_the_job_has_an_active_run_is_refused(db, runtime_settings) -> None:
    posting = await db.seed_posting()
    original = await db.seed_run(posting, status="FAILED", submission_mode="review")
    active = await db.seed_run(posting, status="RUNNING", submission_mode="testing")  # dedup ignores testing runs
    temporal = FakeTemporal()
    async with api_client(temporal) as client:
        resp = await client.post(f"/runs/{original}/retry")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == {"code": "run_in_flight", "message": resp.json()["detail"]["message"],
                                     "run_id": str(active)}
    assert temporal.started == []
    assert len(await db.runs()) == 2


async def test_post_jobs_while_a_testing_run_is_in_flight_is_refused(db, runtime_settings, offline_urls) -> None:
    url = "https://jobs.harness.example/acme/in-flight"
    active = await db.seed_run(await db.seed_posting(url), status="PENDING", submission_mode="testing")
    async with api_client(FakeTemporal()) as client:
        resp = await client.post("/jobs", json={"job_url": url})
    assert resp.status_code == 409, resp.text
    assert (resp.json()["detail"]["code"], resp.json()["detail"]["run_id"]) == ("run_in_flight", str(active))


async def test_a_run_whose_workflow_never_started_is_removed(db, runtime_settings, offline_urls) -> None:
    async with api_client(FailingTemporal()) as client:
        resp = await client.post("/jobs", json={"job_url": "https://jobs.harness.example/acme/down"})
    assert resp.status_code == 503, resp.text
    assert await db.runs() == []  # no orphan PENDING row holding the job


async def test_confirm_not_submitted_lets_the_job_be_applied_to_again(db, runtime_settings, offline_urls) -> None:
    url = "https://jobs.harness.example/acme/again"
    posting = await db.seed_posting(url)
    uncertain = await db.seed_run(posting, status="SUBMIT_UNCERTAIN", submit_attempted_at=NOW,
                                  failure_reason="submit_unverified", submission_mode="review")
    await db.insert("submission_claim", posting_key=str(posting), run_id=uncertain, claimed_at=NOW)
    async with api_client(FakeTemporal()) as client:
        refused = await client.post("/jobs", json={"job_url": url})
        resolved = await client.post(f"/runs/{uncertain}/confirm-not-submitted")
        accepted = await client.post("/jobs", json={"job_url": url})
    assert (refused.status_code, refused.json()["detail"]["code"]) == (409, "already_applied")
    assert resolved.status_code == 200, resolved.text
    assert accepted.status_code == 201, accepted.text
    row = await db.run(uncertain)
    assert (row["status"], row["submit_attempted_at"]) == ("FAILED", None)  # FAILED: never attempted (§1)
    assert "attempted at" in row["error_message"]


async def test_confirm_submitted_completes_and_keeps_the_job_blocked(db) -> None:
    from yahbah.run_state import possibly_applied

    posting = await db.seed_posting()
    uncertain = await db.seed_run(posting, status="SUBMIT_UNCERTAIN", submit_attempted_at=NOW,
                                  failure_reason="submit_unverified", submission_mode="review")
    async with api_client(FakeTemporal()) as client:
        resp = await client.post(f"/runs/{uncertain}/confirm-submitted")
        body = (await client.get(f"/runs/{uncertain}")).json()
    assert resp.status_code == 200, resp.text
    assert (body["status"], body["outcome"], body["failure_reason"]) == ("COMPLETED", "submitted", None)
    async with db.session() as session:
        assert (await possibly_applied(session, posting)).reason == "completed"


# ── delete ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["RUNNING", "AWAITING_REVIEW", "SUBMIT_UNCERTAIN"])
async def test_runs_that_are_active_or_possibly_submitted_cannot_be_deleted(db, status) -> None:
    run_id = await db.seed_run(status=status, submit_attempted_at=NOW if status == "SUBMIT_UNCERTAIN" else None)
    async with api_client(FakeTemporal()) as client:
        resp = await client.delete(f"/runs/{run_id}")
        bulk = await client.post("/runs/bulk-delete", json={"run_ids": [str(run_id)]})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "action_not_allowed"
    assert bulk.json() == {"deleted": 0, "skipped": 1, "success": True}
    assert (await db.run(run_id))["status"] == status


async def test_deleting_a_claimed_run_keeps_its_claim(db, tmp_path) -> None:
    posting = await db.seed_posting()
    run_id = await db.seed_run(posting, status="COMPLETED", submission_mode="review", submit_attempted_at=NOW)
    await db.insert("submission_claim", posting_key=str(posting), run_id=run_id, claimed_at=NOW)
    await db.insert("application_artifact", id=uuid.uuid4(), run_id=run_id, artifact_type="screenshot",
                    path=artifact_file(tmp_path, "s.png", "png"), created_at=NOW)
    async with api_client(FakeTemporal()) as client:
        resp = await client.delete(f"/runs/{run_id}")
    assert resp.status_code == 200, resp.text
    assert await db.fetchall("SELECT run_id FROM submission_claim") == [{"run_id": None}]
    assert await db.fetchall("SELECT id FROM application_artifact") == []


async def test_non_html_downloads_are_not_sniffed(db, tmp_path) -> None:
    run_id = await db.seed_run(status="COMPLETED")
    artifact = await db.insert("application_artifact", id=uuid.uuid4(), run_id=run_id,
                               artifact_type="submitted_values", path=artifact_file(tmp_path, "sv.json", []),
                               created_at=NOW)
    async with api_client(FakeTemporal()) as client:
        resp = await client.get(f"/runs/{run_id}/artifacts/{artifact['id']}/download")
    assert resp.status_code == 200
    assert (resp.headers["content-type"], resp.headers["x-content-type-options"]) == ("application/json", "nosniff")
