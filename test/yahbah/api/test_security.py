"""Host checks on the local-only API (DNS-rebinding and LAN exposure).

ASGITransport does not run the lifespan, so no Temporal server is needed, and
/health touches no database.
"""
import httpx
import pytest

from yahbah.api.main import app


async def _health(headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        return await client.get("/health", headers=headers)


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8000"])
async def test_loopback_host_accepted(host):
    resp = await _health({"Host": host})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize("host", ["192.168.1.50", "192.168.1.50:8000", "attacker.example", "localhost.attacker.example"])
async def test_foreign_host_rejected(host):
    resp = await _health({"Host": host})
    assert resp.status_code == 400


@pytest.mark.parametrize("forwarded", ["localhost:3000", "127.0.0.1:3000"])
async def test_next_proxy_from_loopback_accepted(forwarded):
    # Shape of a request through the Next /api rewrite: changeOrigin sets Host
    # to the target and X-Forwarded-Host carries the browser's Host.
    resp = await _health({"Host": "localhost:8000", "X-Forwarded-Host": forwarded})
    assert resp.status_code == 200


@pytest.mark.parametrize("forwarded", ["192.168.1.50:3000", "attacker.example:3000", "localhost:3000, attacker.example"])
async def test_next_proxy_from_foreign_host_rejected(forwarded):
    resp = await _health({"Host": "localhost:8000", "X-Forwarded-Host": forwarded})
    assert resp.status_code == 400
