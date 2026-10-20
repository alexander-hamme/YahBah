"""Discovery gate: the pollers' off switches, admission of scored jobs only, and bounded promotion.

Offline: Gmail, the DB session factory, the scorer and run creation are all faked.
"""
import asyncio
import base64
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from yahbah.gmail import alert_poller, poller
from yahbah.gmail.alert_parser import ExtractedJob

NOW = datetime.now(timezone.utc)
JOB = ExtractedJob(url="https://jobs.example.com/jobs/1", snippet="Senior Engineer at Acme", source="other")


class Recorder:
    def __init__(self, result=None):
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result() if callable(self.result) else self.result


class FakeGmail:
    """Records construction and every Gmail call."""

    def __init__(self, log: list, messages: dict | None = None):
        self.log = log
        self.messages = messages or {}
        log.append("GmailClient()")

    async def _get_message(self, message_id):
        self.log.append(("_get_message", message_id))
        return self.messages[message_id]

    async def _modify_labels(self, message_id, add_label_ids=None, remove_label_ids=None):
        self.log.append(("_modify_labels", message_id, add_label_ids, remove_label_ids))

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.log.append((name, *args))
        return call


class FakeDB:
    """Stands in for AsyncSessionLocal: select() returns *rows*, nothing is found by dedup queries,
    adds and UPDATE statements are recorded."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added: list = []
        self.updates: list = []

    def __call__(self):
        return _FakeSession(self)


class _FakeSession:
    def __init__(self, db: FakeDB):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        if stmt.is_dml:
            self.db.updates.append(stmt)
        rows = self.db.rows
        return SimpleNamespace(scalar_one_or_none=lambda: None,
                               scalars=lambda: SimpleNamespace(all=lambda: list(rows)))

    async def get(self, model, uid):
        return next(r for r in self.db.rows if r.id == uid)

    def add(self, obj):
        self.db.added.append(obj)

    async def commit(self):
        pass


def stop_after(n: int):
    """An `asyncio` stand-in whose sleep() ends the poller loop (CancelledError) on its n-th call."""
    calls: list[float] = []

    async def sleep(seconds):
        calls.append(seconds)
        if len(calls) >= n:
            raise asyncio.CancelledError

    return SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError, calls=calls)


def settings_returning(rt: dict):
    async def get_runtime_settings():
        return rt
    return get_runtime_settings


# ── Off switches: nothing happens at startup (or later) while a poller is disabled ─────────────

@pytest.mark.parametrize("flag", [False, None, "false", "true", 1])
async def test_disabled_alert_poller_touches_nothing(monkeypatch, flag):
    rt = {"job_alert_sender_patterns": ["*@jobright.ai"], "job_alert_backfill_since": "2026-04-16"}
    if flag is not None:
        rt["job_alert_polling_enabled"] = flag
    gmail_log: list = []
    db = FakeDB()
    poll = Recorder((0, 0))
    submit = Recorder("run-id")
    monkeypatch.setattr(alert_poller, "asyncio", stop_after(4))  # startup stagger + 3 idle checks
    monkeypatch.setattr(alert_poller, "get_runtime_settings", settings_returning(rt))
    monkeypatch.setattr(alert_poller, "GmailClient", lambda: FakeGmail(gmail_log))
    monkeypatch.setattr(alert_poller, "AsyncSessionLocal", db)
    monkeypatch.setattr(alert_poller, "poll_for_alert_emails", poll)
    monkeypatch.setattr("yahbah.jobs.submit_job_for_application", submit)

    await alert_poller.run_alert_poller(temporal_client=object())

    assert poll.calls == [] and submit.calls == []
    assert gmail_log == []  # no client built: no reads, no _modify_labels
    assert db.updates == [] and db.added == []


async def test_enabled_alert_poller_ingests_then_promotes_in_its_first_cycle(monkeypatch):
    """Control for the test above: the same harness does see the calls when enabled."""
    rt = {"job_alert_polling_enabled": True, "job_alert_polling_interval_minutes": 30}
    poll, promote = Recorder((0, 0)), Recorder(0)
    fake_asyncio = stop_after(2)
    monkeypatch.setattr(alert_poller, "asyncio", fake_asyncio)
    monkeypatch.setattr(alert_poller, "get_runtime_settings", settings_returning(rt))
    monkeypatch.setattr(alert_poller, "poll_for_alert_emails", poll)
    monkeypatch.setattr(alert_poller, "_promote_ready_jobs", promote)
    temporal = object()

    await alert_poller.run_alert_poller(temporal_client=temporal)

    assert len(poll.calls) == 1 and promote.calls == [((temporal, rt), {})]
    assert fake_asyncio.calls == [8, 30 * 60]


@pytest.mark.parametrize("flag", [False, None, "true"])
async def test_disabled_status_poller_touches_nothing(monkeypatch, flag):
    rt = {} if flag is None else {"gmail_status_polling_enabled": flag}
    gmail_log: list = []
    db = FakeDB()
    poll = Recorder((0, 0))
    monkeypatch.setattr(poller, "asyncio", stop_after(4))
    monkeypatch.setattr(poller, "get_runtime_settings", settings_returning(rt))
    monkeypatch.setattr(poller, "GmailClient", lambda: FakeGmail(gmail_log))
    monkeypatch.setattr(poller, "AsyncSessionLocal", db)
    monkeypatch.setattr(poller, "poll_for_status_emails", poll)

    await poller.run_status_poller()

    assert poll.calls == [] and gmail_log == [] and db.added == []


async def test_enabled_status_poller_polls_in_its_first_cycle(monkeypatch):
    poll = Recorder((0, 0))
    monkeypatch.setattr(poller, "asyncio", stop_after(2))
    monkeypatch.setattr(poller, "get_runtime_settings", settings_returning({"gmail_status_polling_enabled": True}))
    monkeypatch.setattr(poller, "poll_for_status_emails", poll)

    await poller.run_status_poller()

    assert len(poll.calls) == 1


# ── Admission: only scored jobs are promotable ──────────────────────────────────────────────

@pytest.fixture
def page(monkeypatch):
    fetch = Recorder("A long job description " * 10)
    monkeypatch.setattr(alert_poller, "fetch_job_description", fetch)
    return fetch


async def test_scorer_raising_makes_the_job_unpromotable(monkeypatch, page):
    async def boom(profile_text, text):
        raise TimeoutError("ollama")
    monkeypatch.setattr(alert_poller, "score_job", boom)

    a = await alert_poller._evaluate_job(JOB, profile_text="profile", min_score=0)

    assert (a.status, a.match_score, a.match_rationale) == ("SKIPPED", None, "not scored: TimeoutError")


async def test_scorer_returning_no_score_makes_the_job_unpromotable(monkeypatch, page):
    monkeypatch.setattr(alert_poller, "score_job", Recorder((None, None)))

    a = await alert_poller._evaluate_job(JOB, profile_text="profile", min_score=0)

    assert (a.status, a.match_score, a.match_rationale) == ("SKIPPED", None, "not scored: no score returned")


async def test_nothing_to_score_makes_the_job_unpromotable(monkeypatch):
    monkeypatch.setattr(alert_poller, "fetch_job_description", Recorder(None))
    score = Recorder((80, "fit"))
    monkeypatch.setattr(alert_poller, "score_job", score)

    a = await alert_poller._evaluate_job(
        ExtractedJob(url=JOB.url, snippet="", source="other"), profile_text="profile", min_score=0)

    assert a.status == "SKIPPED" and a.match_rationale.startswith("not scored:") and score.calls == []


@pytest.mark.parametrize("score, min_score, status", [
    (30, 0, "SCHEDULED"),   # 0 = admit every scored job (the user's setting is kept)
    (30, 50, "SKIPPED"),
    (50, 50, "SCHEDULED"),
    (92, 50, "SCHEDULED"),
])
async def test_scored_jobs_follow_the_threshold(monkeypatch, page, score, min_score, status):
    monkeypatch.setattr(alert_poller, "score_job", Recorder((score, "rationale")))

    a = await alert_poller._evaluate_job(JOB, profile_text="profile", min_score=min_score)

    assert (a.status, a.match_score, a.match_rationale) == (status, score, "rationale")


def alert_message(message_id: str, *, age_days: float) -> dict:
    html = '<a href="https://jobs.example.com/jobs/1">Senior Engineer</a>'
    return {
        "id": message_id,
        "internalDate": str(int((time.time() - age_days * 86400) * 1000)),
        "payload": {
            "mimeType": "text/html",
            "headers": [{"name": "From", "value": "Jobright <alerts@jobright.ai>"},
                        {"name": "Subject", "value": "New jobs for you"}],
            "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()},
        },
    }


async def process(gmail, message_id, **overrides):
    kwargs = dict(sender_patterns=["*@jobright.ai"], profile_text="profile", alert_label_id="Label_1",
                  auto_archive=True, min_score=0, delay_hours=24, max_age_days=14)
    return await alert_poller._process_message(gmail, message_id, **(kwargs | overrides))


async def test_unscored_job_from_an_alert_is_stored_skipped(monkeypatch, page):
    async def boom(profile_text, text):
        raise RuntimeError("model unavailable")
    db, log = FakeDB(), []
    monkeypatch.setattr(alert_poller, "AsyncSessionLocal", db)
    monkeypatch.setattr(alert_poller, "extract_job_urls", Recorder([JOB]))
    monkeypatch.setattr(alert_poller, "score_job", boom)

    queued = await process(FakeGmail(log, {"m1": alert_message("m1", age_days=1)}), "m1")

    assert queued == 0
    [row] = db.added
    assert (row.status, row.match_score, row.match_rationale) == ("SKIPPED", None, "not scored: RuntimeError")


async def test_stale_alert_is_not_processed(monkeypatch, page):
    db, log = FakeDB(), []
    extract = Recorder([JOB])
    monkeypatch.setattr(alert_poller, "AsyncSessionLocal", db)
    monkeypatch.setattr(alert_poller, "extract_job_urls", extract)

    queued = await process(FakeGmail(log, {"m1": alert_message("m1", age_days=30)}), "m1")

    assert queued == 0 and extract.calls == [] and page.calls == [] and db.added == []
    assert [entry for entry in log if entry[0] == "_modify_labels"] == []


# ── Promotion: capped per cycle, stale rows expire ──────────────────────────────────────────

def scheduled_row(*, created_days_ago: float, promote_minutes_ago: float, status="SCHEDULED", hold=False):
    return SimpleNamespace(
        id=uuid.uuid4(), job_url=f"https://jobs.example.com/jobs/{uuid.uuid4().hex[:8]}",
        status=status, hold=hold, promoted_run_id=None,
        created_at=NOW - timedelta(days=created_days_ago),
        promote_after=NOW - timedelta(minutes=promote_minutes_ago),
    )


def test_plan_caps_promotions_oldest_first_and_expires_stale_rows():
    fresh = [scheduled_row(created_days_ago=2, promote_minutes_ago=m) for m in range(8)]
    stale = scheduled_row(created_days_ago=30, promote_minutes_ago=40_000)
    not_ready = scheduled_row(created_days_ago=0.1, promote_minutes_ago=-600)
    held = scheduled_row(created_days_ago=2, promote_minutes_ago=100, hold=True)
    unscored = scheduled_row(created_days_ago=2, promote_minutes_ago=100, status="SKIPPED")

    expire, promote = alert_poller.plan_promotions(
        [*fresh, stale, not_ready, held, unscored], now=NOW, max_age_days=14, cap=5)

    assert expire == [stale]
    assert promote == sorted(fresh, key=lambda r: r.promote_after)[:5]


@pytest.mark.parametrize("cap", [0, -1])
def test_plan_with_no_cap_promotes_nothing(cap):
    rows = [scheduled_row(created_days_ago=1, promote_minutes_ago=5)]
    assert alert_poller.plan_promotions(rows, now=NOW, max_age_days=14, cap=cap) == ([], [])


async def test_promotion_cycle_starts_at_most_cap_runs_and_expires_stale_rows(monkeypatch):
    fresh = [scheduled_row(created_days_ago=2, promote_minutes_ago=m) for m in range(8)]
    stale = scheduled_row(created_days_ago=30, promote_minutes_ago=40_000)
    db = FakeDB([*fresh, stale])
    submit = Recorder(lambda: str(uuid.uuid4()))
    monkeypatch.setattr(alert_poller, "AsyncSessionLocal", db)
    monkeypatch.setattr("yahbah.jobs.submit_job_for_application", submit)

    promoted = await alert_poller._promote_ready_jobs(object(), {})  # defaults: cap 5, 14 days

    assert promoted == 5 and len(submit.calls) == 5
    assert stale.job_url not in [args[0] for args, _ in submit.calls]
    assert sum(r.status == "PROMOTED" for r in fresh) == 5
    [expiry] = db.updates
    params = expiry.compile().params
    assert "EXPIRED" in params.values() and [stale.id] in params.values()


async def test_promotion_without_temporal_does_nothing(monkeypatch):
    db = FakeDB([scheduled_row(created_days_ago=30, promote_minutes_ago=5)])
    monkeypatch.setattr(alert_poller, "AsyncSessionLocal", db)
    assert await alert_poller._promote_ready_jobs(None, {}) == 0
    assert db.updates == []
