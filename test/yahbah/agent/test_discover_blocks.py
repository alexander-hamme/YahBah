"""Discover's dead ends and the Greenhouse board route, offline.

A company career page behind bot protection (run 83a0efc2: Akamai's "Access Denied" on a ?gh_jid=
Rubrik page, which the models called job_closed) must end `blocked` from evidence in code, unless the
board's own form can be opened instead (checked in the browser: Greenhouse's host, the job's id, the
form's signature); a job the board itself reports closed must end `job_closed`. Neither may be left to
a model's reading of the page. Pages: test/yahbah/fixtures/discover/; Greenhouse's host is served by the
fixture server (Chrome's host-resolver rules), so the board's real URL patterns are exercised.
"""
import json
from pathlib import Path

import pytest
from browser_use import BrowserSession

from harness.browser import load, offline_profile
from harness.server import Response
from yahbah.agent import boards, discover as discover_mod
from yahbah.agent.boards import (
    GENERIC,
    GREENHOUSE,
    greenhouse_job_id,
    greenhouse_route_verified,
    greenhouse_routes,
    greenhouse_token_candidates,
)
from yahbah.agent.discover import PageEvidence, StageFailed, Trace, classify_page, discover
from yahbah.config import settings

FIXTURES = Path(__file__).parents[1] / "fixtures"
PAGES = FIXTURES / "discover"


def page(name: str) -> str:
    return (PAGES / name).read_text()


def classify_block(status, html, url, controls=0):
    """classify_page on raw HTML only (no rendered text), as a block page is first seen."""
    return classify_page(PageEvidence(url, status, "", "", html, controls), GENERIC)


# ── Block classification (pure) ───────────────────────────────────────────────

@pytest.mark.parametrize("name, status, vendor", [
    ("akamai_access_denied.html", 403, "akamai"),
    ("cloudflare_challenge.html", 403, "cloudflare"),
    ("perimeterx_block.html", 403, "perimeterx"),
    ("datadome_captcha.html", 403, "datadome"),
    ("imperva_incident.html", 200, "imperva"),  # the signature decides, whatever the status
])
def test_anti_bot_interstitials_are_recognised_by_vendor(name, status, vendor) -> None:
    verdict = classify_block(status, page(name), "https://careers.example.com/jobs/1")
    assert verdict is not None and (verdict.reason, verdict.signal) == ("blocked", vendor)
    assert f"HTTP {status}" in verdict.detail and "careers.example.com" in verdict.detail


@pytest.mark.parametrize("name", [
    "greenhouse_board_error.html", "greenhouse_job_not_found.html", "soft_404.html",
    "recaptcha_v3_badge_job.html", "closed_text_with_working_form.html", "login_wall.html",
])
def test_ordinary_and_closed_pages_are_not_blocks(name) -> None:
    assert classify_block(200, page(name), "https://example.com/x", controls=3) is None
    # Gone (404/410 on an inert page) is job_closed, never blocked.
    assert classify_block(404, page(name), "https://example.com/x", controls=3).reason == "job_closed"


def test_the_real_greenhouse_form_is_not_a_block() -> None:
    assert classify_block(200, (FIXTURES / "greenhouse_konux.html").read_text(), "https://x", 40) is None


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_refusing_status_on_an_inert_page_is_a_block_whatever_the_text_says(status) -> None:
    verdict = classify_block(status, page("forbidden_says_not_found.html"), "https://example.com/job/1", controls=0)
    assert verdict is not None and (verdict.reason, verdict.signal) == ("blocked", f"http_{status}")


def test_a_refusing_status_on_a_rendered_app_is_not_a_block() -> None:
    # Single-page apps on static hosting answer deep links with 403 and then render normally.
    assert classify_block(403, "<html><body><div id=root>…</div></body></html>", "https://x", controls=30) is None


# ── Greenhouse route (pure) ───────────────────────────────────────────────────

def test_greenhouse_token_named_in_the_page_comes_before_the_domain_guess() -> None:
    html = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeinc"></script>'
    assert greenhouse_token_candidates("https://careers.acme-corp.com/job?gh_jid=1", html) == \
        ["acmeinc", "acme-corp", "acmecorp"]
    assert greenhouse_token_candidates("https://www.rubrik.com/company/careers/job.8274601?gh_jid=8274601", "") == \
        ["rubrik"]
    assert greenhouse_token_candidates("https://jobs.acme.co.uk/x?gh_jid=1", "") == ["acme"]


def test_greenhouse_closed_page_is_recognised_by_its_url() -> None:
    assert GREENHOUSE.closed_at("https://job-boards.greenhouse.io/embed/job_board?for=rubrik&error=true")
    assert GREENHOUSE.closed_at("https://job-boards.greenhouse.io/acme?error=true")
    assert not GREENHOUSE.closed_at("https://job-boards.greenhouse.io/embed/job_app?for=rubrik&token=8274601")


@pytest.mark.parametrize("url, job_id", [
    ("https://careers.acme.com/jobs?gh_jid=4242", "4242"),
    ("https://www.rubrik.com/company/careers/departments/job.8274601", "8274601"),
    ("https://careers.acme.com/jobs/8274601/", "8274601"),
    ("https://careers.acme.com/jobs/job.1111111?gh_jid=4242", "4242"),  # gh_jid wins over the path
    ("https://careers.acme.com/2024/05/engineer", None),                # dates are no job ids
    ("https://careers.acme.com/jobs/12345", None),                      # too short
    ("https://careers.acme.com/jobs?id=8274601", None),                 # the path only
    ("https://careers.acme.com/jobs?gh_jid=123abc", None),
])
def test_greenhouse_job_id_from_gh_jid_or_a_job_id_shaped_path_number(url, job_id) -> None:
    assert greenhouse_job_id(url) == job_id


FORM = '<form id="application-form"><input type="hidden" name="job_id" value="{id}"></form>'


@pytest.mark.parametrize("landed, html, ok", [
    ("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=4242", FORM.format(id=1), True),  # id in URL
    ("https://job-boards.greenhouse.io/embed/job_app?for=acme&gh_src=x", FORM.format(id=4242), True),  # id in page
    ("https://job-boards.eu.greenhouse.io/acme/jobs/4242", FORM.format(id=1), True),
    ("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=9999", FORM.format(id=9999), False),  # other job
    ("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=42420", FORM.format(id=42420), False),
    ("https://careers.acme.com/apply?token=4242", FORM.format(id=4242), False),  # not Greenhouse's host
    ("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=4242", "<form></form>", False),  # no signature
])
def test_a_route_page_is_verified_by_host_job_id_and_signature(landed, html, ok) -> None:
    assert greenhouse_route_verified(landed, html, "4242") is ok


async def test_greenhouse_routes_put_the_page_named_token_first_and_mark_explicit_pairs() -> None:
    async def wrapper_html(_js):
        return '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeinc"></script>'
    routes = await greenhouse_routes("https://careers.acme-corp.com/job?gh_jid=4242", wrapper_html, False)
    assert [(r.label, r.explicit) for r in routes] == [("acmeinc", True), ("acme-corp", False), ("acmecorp", False)]
    assert routes[0].url == "https://job-boards.greenhouse.io/embed/job_app?for=acmeinc&token=4242"
    assert len(await greenhouse_routes("https://a.example-one.com/x?gh_jid=1", wrapper_html, False)) <= boards.MAX_ROUTES

    async def no_html(_js):
        return ""
    path_url = "https://www.rubrik.com/company/careers/departments/job.8274601"
    assert await greenhouse_routes(path_url, no_html, False) == []  # path ids only for a dead-end page
    [route] = await greenhouse_routes(path_url, no_html, True)
    assert (route.label, route.job_id, route.explicit) == ("rubrik", "8274601", False)
    assert await greenhouse_routes("https://job-boards.greenhouse.io/x/jobs/5?gh_jid=5", no_html, False) == []


# ── discover() on served pages ────────────────────────────────────────────────

GH_HOST = "job-boards.greenhouse.io"


@pytest.fixture(scope="module")
async def gh_browser(fixture_server):
    rules = (f"--host-resolver-rules=MAP {GH_HOST} 127.0.0.1:{fixture_server.port} , "
             "MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1")
    session = BrowserSession(browser_profile=offline_profile(args=[rules]))
    await session.start()
    yield session
    await session.kill()


@pytest.fixture
def no_models(monkeypatch):
    """Dead ends are decided in code: a model tier being reached fails the test."""
    async def forbidden(*_a, **_k):
        raise AssertionError("a model tier ran on a page code should have settled")
    monkeypatch.setattr(discover_mod, "oneshot_navigate", forbidden)
    monkeypatch.setattr(discover_mod, "run_agent", forbidden)


@pytest.fixture
def gh(monkeypatch, pages, fixture_server):
    """Greenhouse's form URL served from the test's pages on Greenhouse's own host. From a wrapper on
    127.0.0.1 the one guessed board token is "127"."""
    monkeypatch.setattr(boards, "GREENHOUSE_FORM", f"http://{GH_HOST}/{pages.prefix}/job_app.html"
                                                   "?for={token}&token={job_id}")

    def closed_marker(status: int, token: str = "127") -> str:
        """job_app.html redirects to the board's closed-job page, which answers `status`."""
        embed = pages.dir / "embed"
        embed.mkdir(exist_ok=True)
        (embed / "job_board").write_text(page("greenhouse_board_error.html"))
        monkeypatch.setitem(fixture_server.mounts, "/embed/", embed)
        monkeypatch.setitem(fixture_server.responses, "/embed/job_board",
                            Response(status, (("Content-Type", "text/html"),)))
        marker = f"http://{GH_HOST}/embed/job_board?for={token}&error=true"
        pages.redirect("job_app.html", marker)
        return marker
    return closed_marker


def walled(pages, query: str = "?gh_jid=4242424", name: str = "acme.html") -> str:
    return pages.write(name, page("akamai_access_denied.html"), status=403) + query


def gh_form(job_id: str) -> str:
    return (f'<!doctype html><title>Job Application</title><form id="application-form">'
            f'<input type="hidden" name="job_id" value="{job_id}">'
            '<label for="a">First name</label><input id="a"><label for="b">Last name</label><input id="b">'
            '<label for="c">Email</label><input id="c" type="email"></form>')


def last_observation() -> dict:
    lines = (Path(settings.artifacts_dir) / "_observations.jsonl").read_text().splitlines()
    return json.loads(lines[-1])


@pytest.mark.browser
@pytest.mark.parametrize("name, vendor", [
    ("akamai_access_denied.html", "akamai"), ("cloudflare_challenge.html", "cloudflare"),
])
async def test_an_interstitial_ends_blocked_without_asking_a_model(browser, pages, no_models, name, vendor) -> None:
    url = pages.write(name, page(name), status=403)
    await load(browser, url)
    with pytest.raises(StageFailed) as exc:
        await discover(browser, Trace())
    assert exc.value.reason == "blocked"
    assert vendor in exc.value.detail and "403" in exc.value.detail
    assert exc.value.detail.endswith("; board id in URL: no")
    assert last_observation() | {"t": None} == {"t": None, "event": "blocked", "url": url, "board": "generic",
                                                  "signal": vendor, "board_id_in_url": False}


@pytest.mark.browser
@pytest.mark.parametrize("query", ["?gh_jid=4242424", ""], ids=["gh_jid", "path_id"])
async def test_a_walled_wrapper_naming_a_job_opens_the_greenhouse_form(gh_browser, pages, no_models, gh,
                                                                       query) -> None:
    """gh_jid, or (F3) a job-id-shaped path number like Rubrik's job.8274601."""
    pages.write("job_app.html", gh_form("4242424"))
    (pages.dir / "job.4242424").mkdir()
    wrapper = walled(pages, query, name="job.4242424/index.html" if not query else "acme.html")
    await load(gh_browser, wrapper)
    trace = Trace()
    result = await discover(gh_browser, trace)
    assert result.board.name == "greenhouse"
    assert result.page_url == f"http://{GH_HOST}/{pages.prefix}/job_app.html?for=127&token=4242424"
    assert "Access Denied" not in (result.job.get("description") or "")  # job info from the form's page
    assert any(r["note"].startswith("greenhouse: opened its own form") for r in trace.rows)


@pytest.mark.browser
async def test_another_jobs_form_is_never_used(gh_browser, pages, no_models, gh) -> None:
    pages.write("job_app.html", gh_form("9999999"))
    pages.redirect("job_app.html", f"http://{GH_HOST}/{pages.prefix}/other.html?for=127&token=9999999")
    pages.write("other.html", gh_form("9999999"))
    await load(gh_browser, walled(pages))
    with pytest.raises(StageFailed) as exc:
        await discover(gh_browser, Trace())
    assert exc.value.reason == "blocked"
    assert "; board route: unverified" in exc.value.detail
    assert "9999999" not in await gh_browser.get_current_page_url()


@pytest.mark.browser
@pytest.mark.parametrize("status", [200, 404])
async def test_a_guessed_board_on_the_closed_job_page_leaves_the_wrapper_to_decide(gh_browser, pages, no_models, gh,
                                                                                  status) -> None:
    """A guessed token may be another company's board: its closed-job page is no evidence the job closed."""
    gh(status)
    await load(gh_browser, walled(pages))
    with pytest.raises(StageFailed) as exc:
        await discover(gh_browser, Trace())
    assert exc.value.reason == "blocked"
    note = "closed on 127" if status == 200 else "none"
    assert exc.value.detail.endswith(f"; board route: {note}; board id in URL: yes")
    assert last_observation()["board_id_in_url"] is True


@pytest.mark.browser
async def test_the_board_named_by_the_page_on_its_closed_job_page_ends_job_closed(gh_browser, pages, no_models,
                                                                                 gh) -> None:
    """Both halves explicit (id from gh_jid, token named by the page) and the board answers 200."""
    gh(200, token="acme")
    wrapper = pages.write("acme.html", '<!doctype html><title>Engineer</title><h1>Engineer</h1><script '
                                       'src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>')
    await load(gh_browser, wrapper + "?gh_jid=4242424")
    with pytest.raises(StageFailed) as exc:
        await discover(gh_browser, Trace())
    assert exc.value.reason == "job_closed"
    assert exc.value.detail.endswith("; board route: closed on acme")
