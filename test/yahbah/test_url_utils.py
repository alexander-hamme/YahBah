"""URL intake policy, fetching under it, and job-URL identity. Offline: a fake resolver and
httpx.MockTransport stand in for DNS and the network."""
import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from yahbah.agent.boards import GREENHOUSE
from yahbah.api.routes import jobs as jobs_route
from yahbah.api.routes import scheduled as scheduled_route
from yahbah.db.session import get_session
from yahbah.url_utils import (
    UnsafeURLError,
    canonicalize_url,
    fetch_public,
    normalize_job_url,
    validate_job_url,
)

PUBLIC_IP = "93.184.215.14"
DNS = {
    "boards.greenhouse.io": [PUBLIC_IP],
    "job-boards.greenhouse.io": [PUBLIC_IP],
    "jobs.example.com": [PUBLIC_IP, "2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
    "careers.example.com": [PUBLIC_IP],
    "intranet.example.com": ["10.1.2.3"],
    "split.example.com": [PUBLIC_IP, "192.168.0.10"],
    "rebound.example.com": ["127.0.0.1"],
    "ula.example.com": ["fd00::1"],
}


async def fake_resolver(host: str) -> list[str]:
    if host not in DNS:
        raise socket.gaierror(socket.EAI_NONAME, f"unknown host {host}")
    return DNS[host]


def mock_transport(routes: dict[str, httpx.Response], seen: list[str]) -> httpx.MockTransport:
    """Serves *routes* by exact URL (404 otherwise) and records every URL requested."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))
    return httpx.MockTransport(handler)


def redirect(location: str) -> httpx.Response:
    return httpx.Response(302, headers={"Location": location})


# ── Policy ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/jobs/x",
    "ftp://jobs.example.com/jobs/1",
    "javascript:alert(1)",
    "data:text/html,<h1>jobs</h1>",
    "jobs.example.com/jobs/1",
    "http:///jobs/1",
    "https://jobs.example.com:99999/jobs/1",
    "https://user:secret@jobs.example.com/jobs/1",
    "http://localhost:8000/jobs/1",
    "http://LOCALHOST./jobs/1",
    "http://app.localhost/jobs/1",
    "http://127.0.0.1:8000/jobs/anything",
    "http://127.1/jobs/1",
    "http://2130706433/jobs/1",
    "http://0x7f000001/jobs/1",
    "http://0.0.0.0/jobs/1",
    "http://10.0.0.5/careers/1",
    "http://172.16.4.2/careers/1",
    "http://192.168.1.1/apply/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/jobs/1",
    "http://224.0.0.1/jobs/1",
    "http://240.0.0.1/jobs/1",
    "http://[::1]/jobs/1",
    "http://[fe80::1]/jobs/1",
    "http://[fd00::1]/jobs/1",
    "http://[ff02::1]/jobs/1",
    "http://[::ffff:127.0.0.1]/jobs/1",
    "http://[64:ff9b::a00:1]/jobs/1",
    "https://intranet.example.com/jobs/1",
    "https://split.example.com/jobs/1",
    "https://ula.example.com/jobs/1",
    "https://no-such-host.example.com/jobs/1",
])
async def test_policy_rejects(url):
    with pytest.raises(UnsafeURLError):
        await validate_job_url(url, resolver=fake_resolver)


@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/acme/jobs/1234567",
    "https://jobs.example.com/careers/open-roles?gh_jid=111",
    "http://93.184.215.14/jobs/1",
    "https://[2606:4700::1111]/jobs/1",
])
async def test_policy_accepts_public_hosts(url):
    assert await validate_job_url(url, resolver=fake_resolver) == url


# ── Fetching under the policy ───────────────────────────────────────────────

@pytest.mark.parametrize("target", [
    "http://127.0.0.1:11434/api/tags",
    "http://192.168.1.1/apply/x",
    "https://intranet.example.com/jobs/1",
    "file:///etc/passwd",
])
async def test_redirect_into_a_private_target_is_refused_before_it_is_requested(target):
    seen: list[str] = []
    transport = mock_transport({"https://jobs.example.com/r": redirect(target)}, seen)
    with pytest.raises(UnsafeURLError):
        await fetch_public("GET", "https://jobs.example.com/r", resolver=fake_resolver, transport=transport)
    assert seen == ["https://jobs.example.com/r"]


async def test_unsafe_url_is_never_requested():
    seen: list[str] = []
    with pytest.raises(UnsafeURLError):
        await fetch_public("GET", "http://10.0.0.5/jobs/1", resolver=fake_resolver,
                           transport=mock_transport({}, seen))
    assert seen == []


async def test_public_redirect_chain_is_followed():
    seen: list[str] = []
    transport = mock_transport({
        "https://jobs.example.com/r": redirect("https://careers.example.com/next"),
        "https://careers.example.com/next": redirect("/jobs/42"),
        "https://careers.example.com/jobs/42": httpx.Response(200, html="<h1>Engineer</h1>"),
    }, seen)
    page = await fetch_public("GET", "https://jobs.example.com/r", resolver=fake_resolver, transport=transport)
    assert page.url == "https://careers.example.com/jobs/42"
    assert page.status_code == 200 and page.content_type == "text/html"
    assert page.text == "<h1>Engineer</h1>" and not page.truncated
    assert len(seen) == 3


async def test_redirect_loop_is_bounded():
    seen: list[str] = []
    transport = mock_transport({"https://jobs.example.com/loop": redirect("https://jobs.example.com/loop")}, seen)
    with pytest.raises(httpx.TooManyRedirects):
        await fetch_public("GET", "https://jobs.example.com/loop", resolver=fake_resolver, transport=transport)
    assert len(seen) == 11


async def test_response_body_is_capped():
    seen: list[str] = []
    transport = mock_transport({"https://jobs.example.com/big": httpx.Response(200, text="x" * 50_000)}, seen)
    page = await fetch_public("GET", "https://jobs.example.com/big", resolver=fake_resolver,
                              transport=transport, max_bytes=1000)
    assert len(page.text) == 1000 and page.truncated


# ── normalize_job_url ────────────────────────────────────────────────────────

async def test_normalize_follows_redirects_to_the_canonical_url():
    seen: list[str] = []
    transport = mock_transport({
        "https://jobs.example.com/r?utm_source=mail": redirect("https://job-boards.greenhouse.io/acme/jobs/1?gh_src=x"),
        "https://job-boards.greenhouse.io/acme/jobs/1?gh_src=x": httpx.Response(200),
    }, seen)
    url = await normalize_job_url("https://jobs.example.com/r?utm_source=mail",
                                  resolver=fake_resolver, transport=transport)
    assert url == "https://boards.greenhouse.io/acme/jobs/1"


async def test_normalize_refuses_a_redirect_into_the_lan():
    seen: list[str] = []
    transport = mock_transport({"https://jobs.example.com/r": redirect("http://192.168.1.1/apply/x")}, seen)
    with pytest.raises(UnsafeURLError):
        await normalize_job_url("https://jobs.example.com/r", resolver=fake_resolver, transport=transport)


async def test_normalize_falls_back_to_the_original_url_on_network_errors():
    def handler(request):
        raise httpx.ConnectError("down", request=request)
    url = await normalize_job_url("https://jobs.example.com/jobs/1/#apply", resolver=fake_resolver,
                                  transport=httpx.MockTransport(handler))
    assert url == "https://jobs.example.com/jobs/1"


# ── Identity ─────────────────────────────────────────────────────────────────

def test_gh_jid_is_job_identity():
    a = canonicalize_url("https://www.example-co.com/careers/open-roles?gh_jid=111")
    b = canonicalize_url("https://www.example-co.com/careers/open-roles?gh_jid=222&gh_src=abc")
    assert a == "https://www.example-co.com/careers/open-roles?gh_jid=111"
    assert b == "https://www.example-co.com/careers/open-roles?gh_jid=222"
    assert GREENHOUSE.matches_url(a) and GREENHOUSE.matches_url(b)


def test_jobright_impression_id_is_stripped():
    a = canonicalize_url("https://jobright.ai/jobs/info/abc123def456?imp_id=aaaa-1111")
    b = canonicalize_url("https://jobright.ai/jobs/info/abc123def456?imp_id=bbbb-2222&utm_source=x")
    assert a == b == "https://jobright.ai/jobs/info/abc123def456"


def test_imp_id_is_kept_on_other_hosts():
    assert canonicalize_url("https://jobs.example.com/view?imp_id=7") == "https://jobs.example.com/view?imp_id=7"


def test_unknown_params_are_kept_as_written():
    assert (canonicalize_url("https://jobs.example-ats.com/apply?id=42&mode=job&iis=&q=a+b&flag")
            == "https://jobs.example-ats.com/apply?id=42&mode=job&iis=&q=a+b&flag")


@pytest.mark.parametrize("url", [
    "HTTPS://Jobs.Example.COM:443/a/./b/../c/?utm_source=x&q=a+b&x=%2F#frag",
    "https://job-boards.greenhouse.io/acme/jobs/1?gh_jid=1&gh_src=2",
    "https://linkedin.com/comm/jobs/view/123/?trackingId=x&refId=y",
    "https://[2606:4700::1111]:8443/jobs/1",
    "https://jobs.example.com",
    "https://jobs.example.com/?",
    "https://jobs.example.com/a?flag&x=1&&y=",
    "https://jobs.example.com/p?q=%zz&r=%2B",
    "https://jobright.ai/jobs/info/x?imp_id=1&imp_id=2&ref=3",
    "http://jobs.example.com:80//double//slash/",
])
def test_canonicalize_is_idempotent(url):
    once = canonicalize_url(url)
    assert canonicalize_url(once) == once


# ── Call sites: the API rejects unsafe URLs with 422 ─────────────────────────

class _NoDB:
    """A DB session that fails the test if it is used."""

    def __getattr__(self, name):
        pytest.fail(f"DB session used: {name}")


@pytest.fixture
def api(monkeypatch):
    submitted: list[str] = []

    async def no_submit(job_url, temporal):
        submitted.append(job_url)
        return "run"

    monkeypatch.setattr(jobs_route, "submit_job_for_application", no_submit)
    app = FastAPI()
    app.include_router(jobs_route.router, prefix="/jobs")
    app.include_router(scheduled_route.router, prefix="/scheduled")
    app.dependency_overrides[get_session] = lambda: _NoDB()
    app.state.temporal = object()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return SimpleNamespace(client=client, submitted=submitted, app=app)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/jobs/1",
    "http://localhost/jobs/1",
    "http://192.168.1.1/apply/x",
    "file:///etc/jobs/x",
])
async def test_post_jobs_rejects_unsafe_urls(api, url):
    async with api.client as c:
        r = await c.post("/jobs", json={"job_url": url})
    assert r.status_code == 422 and "URL not allowed" in r.json()["detail"]
    assert api.submitted == []


@pytest.mark.parametrize("url", ["http://10.0.0.5/careers/1", "file:///etc/jobs/x"])
async def test_post_scheduled_rejects_unsafe_urls(api, url):
    async with api.client as c:
        r = await c.post("/scheduled", json={"job_url": url})
    assert r.status_code == 422 and "URL not allowed" in r.json()["detail"]


async def test_promoting_a_stored_unsafe_url_is_refused(api):
    """submit_job_for_application re-checks the policy, so a stored row can't reach a workflow."""
    row = SimpleNamespace(status="SCHEDULED", job_url="http://192.168.1.1/apply/x")

    class Session:
        async def get(self, model, uid):
            return row

        async def commit(self):
            pytest.fail("promotion committed")

    api.app.dependency_overrides[get_session] = lambda: Session()
    async with api.client as c:
        r = await c.post("/scheduled/00000000-0000-0000-0000-000000000001/promote")
    assert r.status_code == 422 and row.status == "SCHEDULED"
