"""Alert link extraction and job-page fetching under the URL intake policy. Offline: a fake
resolver and httpx.MockTransport."""
import functools
import socket

import httpx
import pytest

from yahbah import url_utils
from yahbah.gmail import alert_parser

DNS = {"jobs.example.com": ["93.184.215.14"], "intranet.example.com": ["10.1.2.3"]}


async def fake_resolver(host):
    if host not in DNS:
        raise socket.gaierror(socket.EAI_NONAME, host)
    return DNS[host]


@pytest.fixture
def web(monkeypatch):
    """Every URL requested, and the responses to serve (200 empty by default)."""
    seen: list[str] = []
    routes: dict[str, httpx.Response] = {}

    def handler(request):
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(200))

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(alert_parser, "normalize_job_url", functools.partial(
        url_utils.normalize_job_url, resolver=fake_resolver, transport=transport))
    return seen, routes, transport


async def test_unsafe_links_in_an_alert_are_skipped(web):
    seen, routes, _ = web
    routes["https://jobs.example.com/jobs/redirector"] = httpx.Response(
        302, headers={"Location": "http://192.168.1.1/apply/x"})
    html = """
      <a href="https://jobs.example.com/jobs/1?utm_source=alert">Senior Engineer</a>
      <a href="http://127.0.0.1:11434/jobs/x">Totally real job</a>
      <a href="http://192.168.1.1/apply/x">Router job</a>
      <a href="file:///etc/jobs/x">Local job</a>
      <a href="https://intranet.example.com/careers/42">Intranet job</a>
      <a href="https://jobs.example.com/jobs/redirector">Redirects into the LAN</a>
    """

    jobs = await alert_parser.extract_job_urls(html, "other")

    assert [j.url for j in jobs] == ["https://jobs.example.com/jobs/1"]
    assert all(u.startswith("https://jobs.example.com/") for u in seen)


async def test_job_page_fetch_never_follows_a_redirect_into_the_lan(web):
    seen, routes, transport = web
    routes["https://jobs.example.com/jobs/1"] = httpx.Response(302, headers={"Location": "http://10.0.0.5/admin"})

    assert await alert_parser.fetch_job_description(
        "https://jobs.example.com/jobs/1", resolver=fake_resolver, transport=transport) is None
    assert seen == ["https://jobs.example.com/jobs/1"]


async def test_job_page_fetch_reads_text_pages_only(web):
    _, routes, transport = web
    routes["https://jobs.example.com/jobs/1"] = httpx.Response(200, html="<p>" + "Build things. " * 20 + "</p>")
    routes["https://jobs.example.com/jobs/1.pdf"] = httpx.Response(
        200, content=b"%PDF-1.7" + b"x" * 500, headers={"Content-Type": "application/pdf"})

    text = await alert_parser.fetch_job_description(
        "https://jobs.example.com/jobs/1", resolver=fake_resolver, transport=transport)
    assert text and text.startswith("Build things.")
    assert await alert_parser.fetch_job_description(
        "https://jobs.example.com/jobs/1.pdf", resolver=fake_resolver, transport=transport) is None
