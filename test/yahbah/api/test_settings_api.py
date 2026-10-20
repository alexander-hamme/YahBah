"""GET/PUT /settings through the real app (contract §3, §8): effective values only, writes validated by
config.validate_runtime_setting(), unknown keys rejected with the §8 error body. No DB, no Temporal."""
import httpx
import yaml

from yahbah.api.main import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                             base_url="http://localhost")


async def test_get_returns_effective_values_and_only_known_keys(runtime_settings) -> None:
    runtime_settings.set(submission_mode="testing", review_window_hours=1.5)  # the fixture also writes testing_mode
    async with _client() as client:
        resp = await client.get("/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["submission_mode"], body["review_window_hours"]) == ("testing", 1.5)
    assert "testing_mode" not in body  # replaced by submission_mode


async def test_an_invalid_window_falls_back_to_48_hours(runtime_settings) -> None:
    runtime_settings.set(review_window_hours=-3)
    async with _client() as client:
        assert (await client.get("/settings")).json()["review_window_hours"] == 48


async def test_put_writes_a_valid_value_and_returns_effective_settings(runtime_settings) -> None:
    runtime_settings.set(submission_mode="review")
    async with _client() as client:
        resp = await client.put("/settings/submission_mode", json={"value": "testing"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["submission_mode"] == "testing"
        resp = await client.put("/settings/review_window_hours", json={"value": 0.25})
    assert resp.status_code == 200, resp.text
    assert resp.json()["review_window_hours"] == 0.25
    stored = yaml.safe_load(runtime_settings.path.read_text())
    assert (stored["submission_mode"], stored["review_window_hours"]) == ("testing", 0.25)


async def test_put_rejects_invalid_values_without_writing(runtime_settings) -> None:
    before = runtime_settings.path.read_text()
    async with _client() as client:
        for key, value in [("review_window_hours", 0), ("review_window_hours", "soon"), ("review_window_hours", True),
                           ("submission_mode", "auto_reviewed"), ("submission_mode", None)]:
            resp = await client.put(f"/settings/{key}", json={"value": value})
            assert resp.status_code == 422, (key, value, resp.text)
            detail = resp.json()["detail"]
            assert detail["code"] == "invalid_setting" and key in detail["message"], (key, value, detail)
    assert runtime_settings.path.read_text() == before


async def test_put_rejects_unknown_keys_without_writing(runtime_settings) -> None:
    before = runtime_settings.path.read_text()
    async with _client() as client:
        for key in ("testing_mode", "anything_else"):
            resp = await client.put(f"/settings/{key}", json={"value": True})
            assert resp.status_code == 404, resp.text
            assert resp.json()["detail"]["code"] == "unknown_setting"
    assert runtime_settings.path.read_text() == before


async def test_request_validation_errors_use_the_error_body(runtime_settings) -> None:
    """FastAPI's own 422s (a missing body field, a bad query parameter) follow contract §8 too."""
    async with _client() as client:
        missing = await client.put("/settings/submission_mode", json={})
        bad_query = await client.get("/applications", params={"page": 0, "per_page": 1000})
    assert missing.status_code == 422
    assert missing.json() == {"detail": {"code": "invalid_request",
                                         "message": "Invalid request: value: Field required."}}
    assert bad_query.status_code == 422
    detail = bad_query.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert "page:" in detail["message"] and "per_page:" in detail["message"]
