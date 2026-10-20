"""Offline probes of URL normalization, alert link extraction, and verification-code heuristics.

All inputs are synthetic. Network is disabled by patching httpx so normalize_job_url
takes its fallback path (no redirects followed).
"""
import asyncio

import httpx

from yahbah.agent.boards import GREENHOUSE
from yahbah.gmail import alert_parser
from yahbah.gmail.parser import extract_code_heuristic
from yahbah.url_utils import ats_normalize, normalize_job_url, strip_tracking, _normalize_url


class _NoNet(httpx.AsyncClient):
    async def head(self, *a, **k):
        raise httpx.ConnectError("network disabled in probe")

    async def get(self, *a, **k):
        raise httpx.ConnectError("network disabled in probe")


httpx.AsyncClient = _NoNet  # type: ignore[misc]


def section(t):
    print(f"\n=== {t}")


async def main():
    section("normalize_job_url (no network)")
    for u in [
        "https://www.example-co.com/careers/job-posting/?gh_jid=4567890",
        "https://www.example-co.com/careers/open-roles?gh_jid=111",
        "https://www.example-co.com/careers/open-roles?gh_jid=222",
        "https://jobright.ai/jobs/info/abc123def456?imp_id=aaaa-1111",
        "https://jobright.ai/jobs/info/abc123def456?imp_id=bbbb-2222",
        "https://www.linkedin.com/comm/jobs/view/1234567890/?trackingId=xyz&refId=abc",
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Remote/Engineer_R12345?source=LinkedIn",
        "https://jobs.example-ats.com/apply?id=42&mode=job&iis=",
    ]:
        n = await normalize_job_url(u)
        print(f"{u}\n   -> {n}\n   greenhouse.matches_url(normalized)={GREENHOUSE.matches_url(n)}")

    section("_is_job_url on raw hrefs (pattern match happens BEFORE redirect resolution)")
    for href in [
        "https://www.linkedin.com/comm/jobs/view/1234567890/?trackingId=x",
        "https://jobright.ai/jobs/info/abc123?imp_id=1",
        "https://jobright.ai/jobs/recommend?login=1",
        "https://u1234567.ct.sendgrid.net/ls/click?upn=AbCdEf",
        "https://example.us1.list-manage.com/track/click?u=1&id=2&e=3",
        "https://click.example-esp.com/f/a/AbCdEf~~/AAAA~~/RgRk",
        "https://jobs.lever.co/acme/0f1e2d3c-aaaa-bbbb-cccc-1234567890ab",
        "https://jobs.ashbyhq.com/acme/0f1e2d3c-aaaa-bbbb-cccc-1234567890ab",
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Remote/Engineer_R12345",
        "https://boards.greenhouse.io/acme/jobs/1234567",
        "https://www.acme.com/careers/jobs/help-desk-technician-123",
        "https://www.acme.com/careers/jobs/settings-engineer-456",
        "https://www.acme.com/jobs/privacy-counsel-789",
        "http://127.0.0.1:8000/jobs/anything",
        "http://192.168.1.1/apply/x",
        "file:///etc/jobs/x",
    ]:
        print(f"{alert_parser._is_job_url(href)!s:10} {href}")

    section("extract_job_urls on a synthetic digest (no network)")
    html = """
    <html><body>
      <a href="https://jobright.ai/jobs/info/aaa111?imp_id=1">Senior Engineer</a>
      <a href="https://jobright.ai/jobs/info/aaa111?imp_id=2">Senior Engineer (dup card)</a>
      <a href="https://jobright.ai/jobs/recommend?login=1">See more recommendations</a>
      <a href="https://u1.ct.sendgrid.net/ls/click?upn=XYZ">Staff Engineer at Acme</a>
      <a href="http://127.0.0.1:11434/jobs/x">Totally real job</a>
    </body></html>"""
    for j in await alert_parser.extract_job_urls(html, "other"):
        print(j)

    section("extract_code_heuristic (first <h1> matching [A-Za-z0-9]{4,12})")
    for h in [
        "<h1>A1b2C3d4</h1>",
        "<h1>Thanks</h1><p>We received your application.</p>",
        "<h1>Welcome</h1><p>Your account is ready.</p>",
        "<h1>Acme</h1><p>Your security code is 739201</p>",
        "<h1>Confirmation</h1>",
    ]:
        print(f"{extract_code_heuristic(h)!r:14} <- {h[:60]}")

    section("alias-insensitive From check")
    print(alert_parser.sender_is_alert("LinkedIn <jobs-noreply@linkedin.com>", ["*@linkedin.com"]))
    print(alert_parser.sender_is_alert("Anyone <x@jobright.ai>", ["*@jobright.ai"]))


asyncio.run(main())
