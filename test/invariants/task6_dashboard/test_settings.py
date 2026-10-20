"""Runtime settings through the real API app (contract §3, §8): the effective submission_mode fails
closed to review, and writes are validated. The settings file is the test's temp copy."""
import httpx
import pytest

from harness.contract import defect


async def _client():
    from yahbah.api.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                             base_url="http://localhost")  # TrustedHostMiddleware: loopback hosts only


@defect("contract §3, §8", 6, "GET /settings returns the raw file: a missing or invalid submission_mode isn't "
                              "reported as the effective review")
@pytest.mark.parametrize("setting", [None, "yolo"], ids=["missing", "invalid"])
async def test_settings_report_review_when_submission_mode_is_missing_or_invalid(runtime_settings, setting) -> None:
    if setting is None:
        runtime_settings.unset("submission_mode")
    else:
        runtime_settings.set(submission_mode=setting)
    async with await _client() as client:
        resp = await client.get("/settings")
    assert resp.status_code == 200, resp.text
    assert resp.json().get("submission_mode") == "review"
    assert resp.json().get("review_window_hours") == 48


@defect("contract §3, §8", 6, "PUT /settings writes any value: an invalid submission_mode is stored")
async def test_an_invalid_submission_mode_is_rejected_and_not_written(runtime_settings) -> None:
    before = runtime_settings.path.read_text()
    async with await _client() as client:
        resp = await client.put("/settings/submission_mode", json={"value": "yolo"})
    assert 400 <= resp.status_code < 500, resp.text
    assert isinstance(resp.json().get("detail"), dict) and resp.json()["detail"].get("code")
    assert runtime_settings.path.read_text() == before
