"""C7, the acceptance gate (roadmap C7; contract §3, §4, §6, §8): whole runs through Temporal with the
real worker and every real activity, before any unattended live submission.

  - Temporal: a local dev server; yahbah.worker.main() in-process with nothing stubbed.
  - Browser: real headless Chrome, launched by SessionRegistry as in production, offline: the job host
    `jobs.harness.example` is mapped onto the harness fixture server, everything else is unresolvable.
    The job is the mock-ATS form with an instant success page (synthetic/mock_ats.html).
  - Models: ScriptedLLM for every OllamaClient call (mapping, match score, job metadata); browser-use
    agents are never reached on this form. No live model.
  - Gmail: a fake GmailClient that fails the run if a verification code is ever asked for.
  - Database: the disposable test database, with the synthetic applicant seeded.
  - API: the real /jobs and /runs routes, talking to the same Temporal server.

The one deviation from production timing: SessionRegistry.open()'s fixed 3 s render wait is capped at
0.5 s (the mock page is static; discover re-checks the page anyway).
"""
import asyncio
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from temporalio.client import WorkflowExecutionStatus

from harness.api import api_client
from harness.browser import offline_profile
from harness.forms import CappedAsyncio

pytestmark = [pytest.mark.browser, pytest.mark.e2e]

JOB_HOST = "jobs.harness.example"
# Chrome: the job host goes to the fixture server, nothing else resolves (harness.browser.OFFLINE_ARGS).
RESOLVER_RULES = f"--host-resolver-rules=MAP {JOB_HOST} 127.0.0.1 , MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1"
SOURCES = {"first name": "first_name", "last name": "last_name", "email": "email"}
EVIDENCE_KEYS = {"key", "label", "type", "required", "options", "answer", "source", "basis", "generated",
                 "written", "page_value", "ok"}  # contract §8 fill evidence rows


class NoGmail:
    """GmailClient for C7: the mock ATS never asks for a verification code."""

    asked: list = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def wait_for_verification_code(self, *args, **kwargs):
        NoGmail.asked.append(kwargs)
        raise AssertionError("C7: a verification code was asked for")

    async def finalize_code_message(self, message_id: str) -> None:
        raise AssertionError("C7: a verification code was finalized")


def script_models(llm) -> None:
    """Replies for every model call a run makes on the mock-ATS form."""
    def mapping(call) -> dict:
        fields = json.loads(call.user.split("FORM FIELDS (JSON):", 1)[1].split("APPLICANT PROFILE:", 1)[0])
        return {"mappings": [{"key": f["key"], "source": SOURCES[f["label"].rstrip(" *").lower()], "value": "",
                              "confidence": 1.0} for f in fields]}

    llm.on("driver", "MappingResponse", handler=mapping)
    llm.on("driver", "_MatchScoreResponse", handler=lambda call: {"match_score": 80, "match_rationale": "scripted"})
    # No company: the title + company + location dedup (check B) stays out of the way of the other C7 runs.
    llm.on("driver", "_JobMetadata", handler=lambda call: {"title": "Software Engineer", "location": "Boston, MA"})
    llm.on("driver", "_JobTechExtraction", handler=lambda call: {"technologies": ["Python"], "specialties": []})


@dataclass
class Pipeline:
    workflows: object  # harness.temporal.Workflows, with no stubs
    db: object
    server: object
    temporal: object

    def job_url(self, name: str) -> str:
        return f"http://{JOB_HOST}:{self.server.port}/synthetic/mock_ats.html?success=instant&job={name}"

    def api(self):
        return api_client(temporal=self.temporal)

    async def enqueue(self, name: str) -> uuid.UUID:
        async with self.api() as client:
            resp = await client.post("/jobs", json={"job_url": self.job_url(name)})
        assert resp.status_code == 201, resp.text
        return uuid.UUID(resp.json()["run_id"])

    async def submits(self) -> int:
        """Submit events the mock ATS received (it reports each one to the fixture server)."""
        return await self.server.wait_events("submit", 10**6, timeout=0.5)

    async def claims(self) -> list[dict]:
        return await self.db.fetchall("SELECT posting_key, run_id FROM submission_claim")

    async def artifacts(self, run_id) -> list[dict]:
        return await self.db.fetchall("SELECT id, artifact_type, path, artifact_metadata FROM application_artifact "
                                      "WHERE run_id = :id ORDER BY created_at", id=run_id)


@pytest.fixture
async def pipeline(temporal_env, db, runtime_settings, llm, fixture_server, monkeypatch):
    import yahbah.agent.session as session_module
    import yahbah.agent.submit as submit_module
    import yahbah.api.routes.jobs as jobs_route
    from harness.temporal import Stubs, WorkerHarness, Workflows
    from yahbah.url_utils import canonicalize_url

    monkeypatch.setattr(session_module, "BrowserProfile",
                        lambda **kw: offline_profile(**{**kw, "headless": True, "args": [RESOLVER_RULES]}))
    monkeypatch.setattr(session_module, "asyncio", CappedAsyncio(0.5))
    registry = session_module.SessionRegistry()
    monkeypatch.setattr(session_module.SessionRegistry, "_instance", registry)
    monkeypatch.setattr(submit_module, "GmailClient", NoGmail)
    NoGmail.asked = []

    async def canonical_only(url: str) -> str:  # POST /jobs's redirect check is a HEAD request: offline here
        return canonicalize_url(url)

    monkeypatch.setattr(jobs_route, "normalize_job_url", canonical_only)
    await db.seed_profile()
    script_models(llm)

    stubs = Stubs(runtime_settings)
    stubs.names = set()  # nothing stubbed: every activity is the real one
    task_queue = f"c7-{uuid.uuid4().hex[:8]}"
    worker = WorkerHarness(temporal_env, stubs, task_queue, monkeypatch)
    workflows = Workflows(temporal_env, db, stubs, worker, task_queue)
    await worker.start()
    try:
        yield Pipeline(workflows, db, fixture_server, temporal_env.client)
    finally:
        await workflows.cleanup()
        await registry.close_all()
    assert NoGmail.asked == []


# ── testing mode ──────────────────────────────────────────────────────────────

async def test_c7_a_testing_run_fills_the_form_and_completes_without_a_claim_or_a_click(pipeline, runtime_settings):
    runtime_settings.set(submission_mode="testing")
    run_id = await pipeline.enqueue("testing")
    assert await pipeline.workflows.wait_closed(run_id, timeout=120) == WorkflowExecutionStatus.COMPLETED

    row = await pipeline.db.run(run_id)
    assert (row["status"], row["submission_mode"], row["submit_attempted_at"]) == ("COMPLETED", "testing", None)
    assert await pipeline.db.statuses_seen(run_id) == ["PENDING", "RUNNING", "COMPLETED"]
    assert await pipeline.submits() == 0
    assert await pipeline.claims() == []
    kinds = [a["artifact_type"] for a in await pipeline.artifacts(run_id)]
    assert "submitted_values" in kinds and "screenshot" in kinds and "submit_evidence" not in kinds


# ── review mode: approve, then submit ─────────────────────────────────────────

async def test_c7_an_approved_review_run_submits_exactly_once_and_blocks_a_second_run(pipeline, runtime_settings):
    runtime_settings.set(submission_mode="review")
    run_id = await pipeline.enqueue("approved")
    await pipeline.workflows.wait_status(run_id, "AWAITING_REVIEW", timeout=120)

    async with pipeline.api() as client:
        review = (await client.get(f"/runs/{run_id}/review")).json()
        assert (review["status"], review["submission_mode"], review["review_round"]) == (
            "AWAITING_REVIEW", "review", 1), review
        answers = {row["label"].rstrip(" *"): row for row in review["answers"]}
        assert all(set(row) >= EVIDENCE_KEYS for row in review["answers"]), review["answers"]
        assert (answers["First name"]["answer"], answers["First name"]["page_value"]) == ("Jane", "Jane")
        assert answers["Email"]["answer"].startswith("jane.doe+job-"), "the run's email alias was not filled in"
        assert all(row["ok"] for row in review["answers"])
        assert review["new_fields"] == []
        shot = await client.get(f"/runs/{run_id}/artifacts/{review['screenshot_artifact_id']}/download")
        assert shot.status_code == 200 and shot.content.startswith(b"\x89PNG"), "no screenshot of the filled form"
        assert await pipeline.submits() == 0 and await pipeline.claims() == []  # nothing before the approval

        approved = await client.post(f"/runs/{run_id}/approve", json={"review_round": 1})
        assert approved.status_code == 202, approved.text

    assert await pipeline.workflows.wait_closed(run_id, timeout=120) == WorkflowExecutionStatus.COMPLETED
    row = await pipeline.db.run(run_id)
    assert row["status"] == "COMPLETED" and row["submit_attempted_at"] is not None
    assert await pipeline.db.statuses_seen(run_id) == ["PENDING", "RUNNING", "AWAITING_REVIEW", "RUNNING", "COMPLETED"]
    assert await pipeline.submits() == 1, "not exactly one submit click"
    assert await pipeline.claims() == [{"posting_key": str(row["job_posting_id"]), "run_id": run_id}]
    [evidence] = [a for a in await pipeline.artifacts(run_id) if a["artifact_type"] == "submit_evidence"]
    records = [json.loads(line) for line in Path(evidence["path"]).read_text().splitlines()]
    assert records and (records[-1]["verdict"], records[-1]["decided_by"], records[-1]["clicks"]) == (
        "submitted", "code", 1)
    assert pipeline.workflows.stubs.calls == []  # (nothing was stubbed)

    async with pipeline.api() as client:
        again = await client.post("/jobs", json={"job_url": pipeline.job_url("approved")})
    assert again.status_code == 409, again.text
    assert (again.json()["detail"]["code"], again.json()["detail"]["run_id"]) == ("already_applied", str(run_id))


# ── review mode: reject, and an expired window ────────────────────────────────

async def test_c7_a_rejected_run_is_cancelled_and_an_expired_one_sends_nothing(pipeline, runtime_settings,
                                                                             monkeypatch):
    from yahbah.config import settings

    monkeypatch.setattr(settings, "max_concurrent_runs", 2)
    runtime_settings.set(submission_mode="review", review_window_hours=48)
    rejected = await pipeline.enqueue("rejected")
    runtime_settings.set(review_window_hours=0.002)  # 7.2 s; snapshotted per run at creation
    expiring = await pipeline.enqueue("expiring")

    await pipeline.workflows.wait_status(rejected, "AWAITING_REVIEW", timeout=120)
    async with pipeline.api() as client:
        resp = await client.post(f"/runs/{rejected}/reject", json={"review_round": 1, "reason": "not a fit"})
    assert resp.status_code == 202, resp.text

    for run_id in (rejected, expiring):
        assert await pipeline.workflows.wait_closed(run_id, timeout=120) == WorkflowExecutionStatus.COMPLETED
    assert [(r["status"], r["failure_reason"], r["submit_attempted_at"]) for r in
            [await pipeline.db.run(rejected), await pipeline.db.run(expiring)]] == [
        ("CANCELLED", "review_rejected", None), ("EXPIRED", "review_expired", None)]
    assert "AWAITING_REVIEW" in await pipeline.db.statuses_seen(expiring)
    assert await pipeline.submits() == 0
    assert await pipeline.claims() == []
    assert not [a for run_id in (rejected, expiring) for a in await pipeline.artifacts(run_id)
                if a["artifact_type"] == "submit_evidence"]
