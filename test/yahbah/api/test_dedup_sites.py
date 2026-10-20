"""Every enqueue path refuses a job that was, or may have been, applied to (contract §5): POST /scheduled,
promotion through the API and by the alert poller, and the alert poller's "already applied" check.
(POST /jobs and check_duplicate_activity: test/invariants/task3_run_lifecycle/test_dedup_and_claim.py.)
Real routes and pollers on the test database; Gmail and Temporal are fakes."""
import base64
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from harness.api import FakeTemporal

NOW = datetime.now(timezone.utc)
URL = "https://jobs.harness.example/acme/applied-1"


@pytest.fixture
async def attempted(db):
    """A run that clicked submit and wasn't verified, for the job at URL."""
    posting = await db.seed_posting(URL)
    return await db.seed_run(posting, status="SUBMIT_UNCERTAIN", submit_attempted_at=NOW, submission_mode="review")


@pytest.fixture
def scheduled_api(monkeypatch, runtime_settings):
    import yahbah.api.routes.scheduled as scheduled_route

    async def unchanged(url: str) -> str:
        return url

    monkeypatch.setattr(scheduled_route, "normalize_job_url", unchanged)  # redirect-following fetch: offline
    app = FastAPI()
    app.include_router(scheduled_route.router, prefix="/scheduled")
    app.state.temporal = FakeTemporal()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://localhost")
    return app, client


async def scheduled_row(db, **values) -> uuid.UUID:
    return (await db.insert("scheduled_job", **{
        "id": uuid.uuid4(), "job_url": URL, "source": "other", "gmail_message_id": "m-1", "status": "SCHEDULED",
        "hold": False, "promote_after": NOW - timedelta(minutes=5), "created_at": NOW - timedelta(hours=25),
        "updated_at": NOW, **values}))["id"]


async def test_adding_a_possibly_applied_job_to_the_queue_is_refused(db, attempted, scheduled_api) -> None:
    _, client = scheduled_api
    async with client:
        resp = await client.post("/scheduled", json={"job_url": URL})
    assert resp.status_code == 409, resp.text
    assert (resp.json()["detail"]["code"], resp.json()["detail"]["run_id"]) == ("already_applied", str(attempted))
    assert await db.fetchall("SELECT id FROM scheduled_job") == []


async def test_promoting_a_possibly_applied_job_is_refused(db, attempted, scheduled_api) -> None:
    app, client = scheduled_api
    job = await scheduled_row(db)
    async with client:
        resp = await client.post(f"/scheduled/{job}/promote")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "already_applied"
    assert app.state.temporal.started == []
    assert (await db.fetchone("SELECT status FROM scheduled_job WHERE id = :id", id=job))["status"] == "SCHEDULED"


async def test_a_promoted_run_is_discovered_and_testing_runs_dont_block_promotion(db, scheduled_api) -> None:
    app, client = scheduled_api
    await db.seed_run(await db.seed_posting(URL), status="COMPLETED", submission_mode="testing")
    job = await scheduled_row(db)
    async with client:
        resp = await client.post(f"/scheduled/{job}/promote")
    assert resp.status_code == 200, resp.text
    [started] = app.state.temporal.started
    run = await db.run(started["arg"].run_id)
    assert (run["origin"], run["status"]) == ("discovered", "PENDING")


async def test_the_poller_skips_promoting_a_possibly_applied_job(db, attempted, runtime_settings) -> None:
    from yahbah.gmail import alert_poller

    job = await scheduled_row(db)
    temporal = FakeTemporal()
    assert await alert_poller._promote_ready_jobs(temporal, {}) == 0
    assert temporal.started == []
    assert (await db.fetchone("SELECT status FROM scheduled_job WHERE id = :id", id=job))["status"] == "SKIPPED"


class _Gmail:
    def __init__(self, message: dict) -> None:
        self.message, self.labelled = message, []

    async def _get_message(self, message_id):
        return self.message

    async def _modify_labels(self, message_id, add_label_ids=None, remove_label_ids=None):
        self.labelled.append(message_id)


async def test_the_poller_does_not_queue_a_possibly_applied_job_again(db, attempted, monkeypatch) -> None:
    from yahbah.gmail import alert_poller
    from yahbah.gmail.alert_parser import ExtractedJob

    async def extract(html, source):
        return [ExtractedJob(url=URL, snippet="Engineer at Acme", source="other")]

    async def never(*args, **kwargs):
        raise AssertionError("a possibly-applied job was fetched and scored for the queue")

    monkeypatch.setattr(alert_poller, "extract_job_urls", extract)
    monkeypatch.setattr(alert_poller, "_evaluate_job", never)
    html = base64.urlsafe_b64encode(f'<a href="{URL}">Engineer</a>'.encode()).decode()
    gmail = _Gmail({"id": "m1", "internalDate": str(int(time.time() * 1000)), "payload": {
        "mimeType": "text/html", "body": {"data": html},
        "headers": [{"name": "From", "value": "Alerts <alerts@jobright.ai>"}, {"name": "Subject", "value": "Jobs"}]}})

    queued = await alert_poller._process_message(
        gmail, "m1", sender_patterns=["*@jobright.ai"], profile_text="profile", alert_label_id="Label_1",
        auto_archive=False, min_score=0, delay_hours=24, max_age_days=14)
    assert queued == 0
    assert await db.fetchall("SELECT id FROM scheduled_job") == []
    assert gmail.labelled == ["m1"]  # the alert itself is still processed


async def test_scheduled_errors_use_the_contract_shape(db, scheduled_api) -> None:
    _, client = scheduled_api
    promoted = await scheduled_row(db, status="PROMOTED")
    async with client:
        bad_id = await client.post("/scheduled/not-a-uuid/approve")
        missing = await client.get(f"/scheduled/{uuid.uuid4()}")
        wrong_state = await client.post(f"/scheduled/{promoted}/hold")
    assert (bad_id.status_code, bad_id.json()["detail"]["code"]) == (422, "invalid_request")
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "scheduled_not_found")
    assert (wrong_state.status_code, wrong_state.json()["detail"]["code"]) == (409, "action_not_allowed")
    assert wrong_state.json()["detail"]["message"]
