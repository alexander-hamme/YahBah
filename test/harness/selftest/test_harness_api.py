"""harness.api: the test app serves the real API with the real app's error handling."""
from fastapi.routing import APIRoute

from harness.api import FakeTemporal, api_client, make_app


def _routes(app) -> set[tuple[str, frozenset]]:
    return {(r.path, frozenset(r.methods)) for r in app.router.routes if isinstance(r, APIRoute)}


def test_the_test_app_has_every_route_of_the_real_app() -> None:
    from yahbah.api.main import app

    assert _routes(make_app()) == _routes(app)


async def test_validation_errors_have_the_contract_error_body() -> None:
    """Contract §8: FastAPI's own 422s are {"detail": {"code": "invalid_request", "message": ...}}."""
    async with api_client(FakeTemporal()) as client:
        resp = await client.post("/jobs", json={})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_request"
    assert "job_url" in resp.json()["detail"]["message"]
