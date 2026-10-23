"""Discover-stage failure reasons: what a page that isn't an application form is, decided by code evidence.

Trigger: run 83a0efc2 hit an Akamai "Access Denied" 403 and the discover model called it job_closed. A model
reading "Access Denied" can't tell a bot wall from a closed job; the HTTP status and the interstitial's
signature can. The rule pinned here (proposed to the main session for contract §2):

  1. A visible application form always wins (whatever the page says or the status is).
  2. A vendor bot-wall signature (Akamai, Cloudflare, PerimeterX/HUMAN, DataDome, Imperva), at any status,
     even when the wall shows a captcha                                          -> blocked
  3. Main document HTTP 401/403/407/429 on an inert page                         -> blocked
  4. Main document HTTP 404/410 on an inert page                                 -> job_closed
     ...but only once the board's own route is exhausted: a 404 on a company wrapper whose URL names a
     board job (?gh_jid=) first tries the board's form (lead's ruling, 2026-10-21); so does a block
     (inert: next to nothing to click; static-hosted SPAs answer deep links 403/404, then render the job)
  5. The board's own closure marker (Greenhouse: board root with ?error=true)     -> job_closed
  6. A visible captcha challenge on the site's own page (not the invisible v3 badge) -> captcha
  7. A visible password field, no application form                              -> login_wall
  8. Main document HTTP 5xx, no vendor signature                                 -> browser_error (retryable)
  9. Otherwise no verdict: discover ends form_not_found, whatever a model said.
  Rules 2-5 and 8 are decided before any model tier (no model calls); 6-7 only after the tiers fail
  (a guest-apply path may exist). A model's job_closed/captcha/login_required is advice, recorded in the
  detail, never the reason.

Interface (yahbah.agent.discover, implemented by nav; agreed via SendMessage):
  PageEvidence(url, status, title, text, html, controls)  frozen dataclass (controls: visible clickable/fillable)
  PageVerdict(reason, signal, detail)                 frozen dataclass
  async read_page_evidence(session) -> PageEvidence   browser collector
  classify_page(evidence, board) -> PageVerdict|None  pure

Cases discover.py doesn't decide yet are strict xfails, switched off per reason in PENDING_REASONS.
Fixtures: test/yahbah/fixtures/discover/ (synthetic, modelled on the public interstitial formats).
"""
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from browser_use import BrowserSession

from harness.browser import load, offline_profile
from yahbah.agent import boards
from yahbah.agent import discover as discover_module
from yahbah.agent.boards import GENERIC, GREENHOUSE, detect_board
from yahbah.agent.discover import StageFailed, Trace, discover
from yahbah.agent.tools import run_js
from yahbah.workflows.activities.browser import _app_error

FIXTURES = Path(__file__).parents[1] / "fixtures" / "discover"

pending = pytest.mark.xfail(strict=True, reason="not implemented yet in discover.py (nav)")
# What discover.py doesn't decide yet; drop a reason once it does (strict: an XPASS fails the run).
# "model_advice": a model's job_closed/captcha/login_required without code evidence ends form_not_found.
PENDING_REASONS: set[str] = set()  # all nine §2 rules are wired (2026-10-21)


def until(reason: str | None) -> list:
    return [pending] if reason in PENDING_REASONS else []


def api(name: str):
    """A symbol of the agreed interface; AttributeError (an xfail) until it exists."""
    return getattr(discover_module, name)


def evidence(url: str = "https://careers.example.com/jobs/1", status: int | None = 200, title: str = "",
             text: str = "", html: str = "", controls: int = 0):
    return api("PageEvidence")(url=url, status=status, title=title, text=text, html=html, controls=controls)


@dataclass(frozen=True)
class Case:
    fixture: str
    status: int
    reason: str | None          # the verdict's reason; None = no verdict (discover goes on to its tiers)
    signal: str | None = None   # a word the verdict's signal must contain (the vendor), if any
    headers: tuple = ()


# Every fixture, as served, through the real collector and the classifier.
CASES = [
    Case("akamai_access_denied.html", 403, "blocked", "akamai", (("Server", "AkamaiGHost"),)),
    Case("cloudflare_challenge.html", 403, "blocked", "cloudflare", (("Server", "cloudflare"), ("cf-mitigated", "challenge"))),
    Case("perimeterx_block.html", 403, "blocked", "perimeterx"),   # a captcha (press & hold), but the vendor's wall
    Case("datadome_captcha.html", 403, "blocked", "datadome"),     # likewise
    Case("imperva_incident.html", 200, "blocked", "imperva"),      # Imperva/Incapsula serves its wall with 200
    Case("too_many_requests.html", 429, "blocked"),
    Case("forbidden_says_not_found.html", 403, "blocked"),         # the status decides, not "Job not found"
    Case("greenhouse_job_not_found.html", 404, "job_closed"),
    Case("greenhouse_job_not_found.html", 410, "job_closed"),
    Case("service_unavailable.html", 503, "browser_error"),
    Case("cloudflare_challenge.html", 503, "blocked", "cloudflare"),  # vendor beats 5xx (Cloudflare's older IUAM page)
    Case("captcha_gate.html", 200, "captcha"),
    Case("login_wall.html", 200, "login_wall"),
    Case("ambiguous_empty.html", 200, None),
    Case("soft_404.html", 200, None),                               # "no longer available" text on a 200 is no evidence
    Case("recaptcha_v3_badge_job.html", 200, None),                 # the invisible v3 badge is no captcha wall
    Case("greenhouse_board_error.html", 200, None),                 # its flash text alone, off greenhouse.io: no evidence
    Case("vendor_words_in_job_text.html", 200, None),               # a job page whose prose quotes WAF pages
]


def case_id(c: Case) -> str:
    return f"{c.fixture.removesuffix('.html')}-{c.status}"


async def serve(pages, case: Case) -> str:
    return pages.write(case.fixture, (FIXTURES / case.fixture).read_text(), status=case.status,
                       headers=dict(case.headers))


async def wait_for_url(browser, url: str, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            href, ready = json.loads(await run_js(browser, "JSON.stringify([location.href, document.readyState])"))
            if href == url and ready == "complete":
                return
        except Exception:  # the old document can vanish under us while navigating
            pass
        await asyncio.sleep(0.05)
    raise TimeoutError(f"never reached {url}")


async def classify_served(browser, url: str):
    ev = await api("read_page_evidence")(browser)
    board = await detect_board(lambda c: run_js(browser, c), url)
    return ev, api("classify_page")(ev, board)


# ── collector + classifier, on every fixture ──────────────────────────────────

@pytest.mark.browser
@pytest.mark.parametrize("case", [pytest.param(c, id=case_id(c), marks=until(c.reason))
                                  for c in CASES])
async def test_each_fixture_gets_the_reason_its_evidence_supports(browser, pages, case) -> None:
    url = await serve(pages, case)
    await load(browser, url)
    ev, verdict = await classify_served(browser, url)

    assert ev.status == case.status and ev.url.startswith(url)
    if case.reason is None:
        assert verdict is None, verdict
        return
    assert verdict is not None and verdict.reason == case.reason, verdict
    if case.signal:
        assert case.signal in verdict.signal.lower()
    if case.status != 200:
        assert str(case.status) in verdict.detail  # the user (and QA) can see what the site answered
    assert url.split("://")[1].split("/")[0] in verdict.detail  # ... and where


@pytest.mark.browser
async def test_the_collector_reports_the_final_document_after_a_redirect(browser, pages) -> None:
    """A closed Greenhouse job 302s to the board root with ?error=true: the evidence is the landing page's."""
    landing = pages.write("acme.html", (FIXTURES / "greenhouse_board_error.html").read_text()) + "?error=true"
    await browser.navigate_to(pages.redirect("acme/jobs/2000003", landing))
    await wait_for_url(browser, landing)
    ev = await api("read_page_evidence")(browser)
    assert (ev.status, ev.url) == (200, landing)
    assert "no longer open" in ev.text


@pytest.mark.browser
async def test_the_collector_decodes_text_the_akamai_page_entity_escapes(browser, pages) -> None:
    """Akamai writes its reference and errors.edgesuite.net URL as HTML entities: `text` must be the rendered text."""
    case = CASES[0]
    await load(browser, await serve(pages, case))
    ev = await api("read_page_evidence")(browser)
    assert "errors.edgesuite.net" in ev.text and "Reference #18." in ev.text


# ── the classifier on its own: precedence and board markers ───────────────────

@pytest.mark.parametrize("url", [
    "https://job-boards.greenhouse.io/acme?error=true",
    "https://job-boards.eu.greenhouse.io/acme?error=true",
    "https://boards.greenhouse.io/acme?error=true",
    "https://job-boards.greenhouse.io/acme/?error=true&utm_source=x",
    # an unknown id on embed/job_app lands here (nav, verified live 2026-10-21)
    "https://job-boards.greenhouse.io/embed/job_board?for=acme&error=true",
    "https://boards.greenhouse.io/embed/job_board?for=acme&error=true",
])
def test_greenhouse_board_root_with_error_true_is_a_closed_job(url) -> None:
    verdict = api("classify_page")(evidence(url, 200, "Jobs at Acme", "The job you are looking for is no longer open."),
                                   GREENHOUSE)
    assert verdict is not None and verdict.reason == "job_closed"
    assert "greenhouse" in verdict.signal.lower()


@pytest.mark.parametrize("url, board", [
    ("https://job-boards.greenhouse.io/acme", GREENHOUSE),                         # board root, no error flag
    ("https://job-boards.greenhouse.io/acme/jobs/2000001?error=true", GREENHOUSE),  # a job page, not the root
    ("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=2000001&error=true", GREENHOUSE),  # the form itself
    ("https://careers.example.com/jobs?gh_jid=2000001&error=true", GREENHOUSE),    # company site: not Greenhouse's flag
    ("https://careers.example.com/acme?error=true", GENERIC),
])
def test_error_true_anywhere_else_is_no_evidence(url, board) -> None:
    assert api("classify_page")(evidence(url, 200, "Jobs", "The job you are looking for is no longer open."), board) is None


@pytest.mark.parametrize("status", [401, 407])
def test_every_refusing_status_is_blocked_on_an_inert_page(status) -> None:
    verdict = api("classify_page")(evidence(status=status, text="Unauthorized"), GENERIC)
    assert verdict is not None and verdict.reason == "blocked" and str(status) in verdict.signal


@pytest.mark.parametrize("marker", [
    "Reference #18.5a1b2c3d.1792605523.8152b77 https://errors.edgesuite.net/18.5a1b2c3d",
    "Request unsuccessful. Incapsula incident ID: 123-456",
    "the cf-error-details block",
])
def test_vendor_markers_quoted_in_the_prose_of_a_live_page_are_no_evidence(marker) -> None:
    """§2: "text on a page is never evidence". A rendered job page (200, 12 controls) quoting a WAF page."""
    ev = evidence(status=200, title="Edge Support Engineer", text=f"Support role. You will read {marker}.",
                  html=f"<main><p>Support role. You will read {marker}.</p><a href='#apply'>Apply</a></main>", controls=12)
    assert api("classify_page")(ev, GENERIC) is None


@pytest.mark.parametrize("status, text, reason", [
    pytest.param(403, "Job not found. This position is no longer available.", "blocked", marks=until("blocked")),  # status beats words
    pytest.param(429, "This job has been closed.", "blocked", marks=until("blocked")),
    pytest.param(404, "Access Denied", "job_closed", marks=until("job_closed")),                                     # likewise the other way round
    pytest.param(200, "This job is no longer accepting applications.", None, marks=until(None)),             # words alone: no verdict
    pytest.param(200, "Access Denied. You don't have permission to access this page.", None, marks=until(None)),
    pytest.param(None, "", None, marks=until(None)),                                                         # status unknown: nothing to go on
    pytest.param(500, "Internal Server Error", "browser_error", marks=until("browser_error")),
    pytest.param(502, "Bad Gateway", "browser_error", marks=until("browser_error")),
])
def test_status_decides_over_page_text(status, text, reason) -> None:
    verdict = api("classify_page")(evidence(status=status, text=text), GENERIC)
    assert (verdict.reason if verdict else None) == reason


@pytest.mark.parametrize("status", [403, 404, 410, 429])
def test_a_status_alone_is_no_evidence_on_a_page_that_rendered_normally(status) -> None:
    """A single-page app on static hosting: the deep link answers 403/404, then the job and its Apply render."""
    ev = evidence(status=status, title="Senior Engineer | Acme", text="Senior Engineer. Apply now.", controls=25)
    assert api("classify_page")(ev, GENERIC) is None


def test_a_vendor_signature_beats_a_not_found_status_and_text() -> None:
    """A WAF answering 404 with its challenge page is a wall, not a closed job."""
    html = (FIXTURES / "cloudflare_challenge.html").read_text()
    verdict = api("classify_page")(evidence(status=404, title="Just a moment...", text="Job not found", html=html),
                                   GENERIC)
    assert verdict is not None and verdict.reason == "blocked" and "cloudflare" in verdict.signal.lower()


def test_a_vendor_signature_is_read_from_markup_not_only_from_the_title() -> None:
    """Retitled interstitials (DataDome uses the site's own host as title) are still recognised by their markup."""
    html = (FIXTURES / "datadome_captcha.html").read_text()
    verdict = api("classify_page")(evidence(status=403, title="careers.example.com", text="", html=html), GENERIC)
    assert verdict is not None and verdict.reason == "blocked" and "datadome" in verdict.signal.lower()


def test_classify_page_is_pure() -> None:
    """No session, no model, not async: classification is a function of plain data, so it is repeatable."""
    ev = evidence(status=403, html=(FIXTURES / "akamai_access_denied.html").read_text())
    first = api("classify_page")(ev, GENERIC)
    assert first is not None and first.reason == "blocked" and first == api("classify_page")(ev, GENERIC)


# ── discover(): code before models, and models never decide the reason ───────

async def run_discover(browser, url: str, trace: Trace | None = None) -> StageFailed | None:
    await load(browser, url)
    try:
        await discover(browser, trace or Trace())
    except StageFailed as exc:
        return exc
    return None


EARLY = [pytest.param(c, id=case_id(c), marks=until(c.reason))
         for c in CASES if c.reason in ("blocked", "job_closed", "browser_error")]


@pytest.mark.browser
@pytest.mark.parametrize("case", EARLY)
async def test_hard_evidence_ends_discover_before_any_model_is_asked(browser, pages, llm, agent_llm, case) -> None:
    """No rule in `llm` and no agent script: any model call fails the test. Run 83a0efc2 spent two
    one-shot calls and two navigation agents (the first form_not_found was retried) on an Akamai wall."""
    failed = await run_discover(browser, await serve(pages, case))
    assert failed is not None and failed.reason == case.reason, failed
    assert llm.calls == [] and agent_llm.calls == []


@pytest.mark.browser
async def test_a_page_that_fails_to_load_is_a_browser_error(browser, pages, llm, agent_llm) -> None:
    """Rule 8, net errors. A failed navigate_to raises (SessionRegistry.open's raise becomes the activity's
    browser_error); a click that lands on Chrome's error page (chrome-error://) must end browser_error too,
    with no model calls."""
    with pytest.raises(RuntimeError, match="ERR_NAME_NOT_RESOLVED"):
        await browser.navigate_to("http://jobs.unresolvable.invalid/apply?id=1")
    start = pages.write("job.html", '<!doctype html><title>Engineer</title><main><h1>Engineer</h1>'
                                    '<a href="http://apply.unresolvable.invalid/form">Apply</a></main>')
    failed = await run_discover(browser, start)
    assert await run_js(browser, "location.href") == "chrome-error://chromewebdata/"  # (browser-use reports the target URL)
    assert failed is not None and failed.reason == "browser_error", failed
    assert llm.calls == [] and agent_llm.calls == []


def test_chrome_error_page_classifies_as_browser_error() -> None:
    verdict = api("classify_page")(evidence(url="chrome-error://chromewebdata/", status=None), GENERIC)
    assert verdict is not None and verdict.reason == "browser_error"


@pytest.mark.browser
@pytest.mark.parametrize("fixture", ["ambiguous_empty.html", "soft_404.html", "greenhouse_board_error.html"])
@pytest.mark.parametrize("claim", [pytest.param(c, marks=until("model_advice")) for c in
                                   ("job_closed", "captcha", "login_required")])
async def test_a_models_claim_without_code_evidence_ends_form_not_found(browser, pages, llm, agent_llm, fixture,
                                                                        claim) -> None:
    url = pages.write(fixture, (FIXTURES / fixture).read_text())
    llm.on("driver", "NavDecision", handler=lambda call: {"reasoning": f"The page says {claim}.", "state": claim})
    agent_llm.done(data={"state": claim, "explanation": f"The posting looks like {claim}."})
    failed = await run_discover(browser, url)
    assert failed is not None and failed.reason == "form_not_found", failed
    assert claim in failed.detail  # the model's advice is kept for the user and for triage


@pytest.mark.browser
@pytest.mark.parametrize("fixture, reason", [pytest.param("captcha_gate.html", "captcha", marks=until("captcha")),
                                             pytest.param("login_wall.html", "login_wall", marks=until("login_wall"))])
async def test_captcha_and_login_walls_are_decided_by_code_after_the_tiers(browser, pages, llm, agent_llm, fixture,
                                                                         reason) -> None:
    """The models shrug ("unknown", "other"); the code evidence still names the wall."""
    url = pages.write(fixture, (FIXTURES / fixture).read_text())
    llm.on("driver", "NavDecision", handler=lambda call: {"reasoning": "unclear", "state": "unknown"})
    agent_llm.done(data={"state": "other", "explanation": "I could not find a form."})
    failed = await run_discover(browser, url)
    assert failed is not None and failed.reason == reason, failed


@pytest.mark.browser
async def test_a_working_form_wins_over_closed_text_and_injected_instructions(browser, pages, llm, agent_llm) -> None:
    """Holds today (the form check comes first); must keep holding once page text is read for evidence."""
    fixture = "closed_text_with_working_form.html"
    url = pages.write(fixture, (FIXTURES / fixture).read_text())
    assert await run_discover(browser, url) is None
    assert llm.calls == [] and agent_llm.calls == []


SPA_FORM = """<!doctype html><html><head><title>Apply</title></head><body><form onsubmit="event.preventDefault()">
<div><label for="a">First name</label> <input id="a"></div><div><label for="b">Last name</label> <input id="b"></div>
<div><label for="c">Email</label> <input id="c" type="email"></div></form></body></html>"""
OPEN_SETTLE = 3  # SessionRegistry.open sleeps this long after navigating, before discover runs


async def open_like_production(browser, url: str) -> StageFailed | None:
    await browser.navigate_to(url)
    await asyncio.sleep(OPEN_SETTLE)
    try:
        await discover(browser, Trace())
    except StageFailed as exc:
        return exc
    return None


@pytest.mark.browser
@pytest.mark.parametrize("status", [403, 404])
@pytest.mark.parametrize("view", ["job", "form"])
async def test_an_spa_deep_link_answered_403_or_404_that_renders_is_not_a_dead_end(
        browser, pages, llm, agent_llm, status, view) -> None:
    """The inert-page gate (rules 3-4): index.html comes back 403/404, then the app renders the route.
    view=job: a job page with an Apply link (code clicks it); view=form: the form itself, 5 controls."""
    pages.write("spa_form.html", SPA_FORM)
    url = pages.write("spa.html", (FIXTURES / "spa_deep_link.html").read_text(), status=status) + f"?view={view}"
    failed = await open_like_production(browser, url)
    assert failed is None, failed
    assert llm.calls == [] and agent_llm.calls == []


@pytest.mark.browser
@pytest.mark.parametrize("status", [403, 404])
async def test_a_form_on_a_refusing_status_still_wins(browser, pages, llm, agent_llm, status) -> None:
    """Rule 1 beats rules 3-4 outright, not only through the inert gate: a small application form
    (4 fields + submit = 5 controls, inert by count) served with 403/404 is a form, not a dead end."""
    fixture = "closed_text_with_working_form.html"
    url = pages.write(fixture, (FIXTURES / fixture).read_text(), status=status)
    failed = await run_discover(browser, url)
    assert failed is None, failed


@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.parametrize("status", [403, 404])
async def test_an_spa_that_renders_after_the_open_settle_is_not_judged_on_its_empty_shell(
        browser, pages, llm, agent_llm, status) -> None:
    """A slow bundle: the app renders 1.5 s after the 3 s settle. A status-only verdict on the still
    empty shell would end blocked/job_closed for a live job; the verdict must wait for the page."""
    pages.write("spa_form.html", SPA_FORM)
    url = pages.write("spa.html", (FIXTURES / "spa_deep_link.html").read_text(), status=status)
    failed = await open_like_production(browser, url + f"?view=job&delay={(OPEN_SETTLE + 1.5) * 1000:.0f}")
    assert failed is None, failed


@pytest.mark.browser
async def test_the_invisible_recaptcha_badge_is_not_a_captcha_wall(browser, pages, llm, agent_llm) -> None:
    """Holds today; guards against a captcha detector that fires on every page carrying reCAPTCHA v3."""
    fixture = "recaptcha_v3_badge_job.html"
    url = pages.write(fixture, (FIXTURES / fixture).read_text())
    llm.on("driver", "NavDecision", handler=lambda call: {"reasoning": "no apply control", "state": "unknown"})
    agent_llm.done(data={"state": "other", "explanation": "No application form here."})
    failed = await run_discover(browser, url)
    assert failed is not None and failed.reason == "form_not_found", failed


# ── retryability of the discover reasons (holds today) ────────────────────────

@pytest.mark.parametrize("reason, retryable", [
    ("blocked", False),        # retrying at once hammers the wall; the user retries later
    ("job_closed", False),
    ("captcha", False),
    ("login_wall", False),
    ("form_not_found", True),  # a slow or half-rendered page can succeed on a second attempt
    ("browser_error", True),   # a crashed browser, a lost page, a 5xx
])
def test_activity_retryability_of_discover_reasons(reason, retryable) -> None:
    assert _app_error(StageFailed(reason, "x")).non_retryable is (not retryable)


# ── the board route (contract §2 "Board route first"): browser-only, verified in the page ──
# gh_browser's Chrome resolves Greenhouse's host and a company's host to the fixture server, so the route's
# real candidate URLs load real pages. boards.GREENHOUSE_FORM is pointed at a per-token page on Greenhouse's
# host (http; the path names the token, the query is the real one), and each token's page is set up by
# `gh_board`: the job's form, another job's form, or a redirect to the board's closed-job marker
# (/<token>?error=true) served with 200 (a real board without the job) or 404 (no such board).
# No part of the route is stubbed; there is no API.

GH_HOST = "job-boards.greenhouse.io"
COMPANY = "careers.acmeco.com"  # its domain guesses the board token "acmeco"
JOB = "2000003"
INERT_403 = "<!doctype html><title>Forbidden</title><h1>Forbidden</h1>"


def gh_form(job_id: str | None, signature: bool = True) -> str:
    """An application form; with Greenhouse's DOM signature (form#application-form) and the job id in the page."""
    hidden = f'<input type="hidden" name="job_id" value="{job_id}">' if job_id else ""
    form_id = ' id="application-form"' if signature else ""
    return (f'<!doctype html><html><head><title>Job Application for Senior Engineer at Acme</title></head><body>'
            f'<form{form_id} onsubmit="event.preventDefault()">{hidden}'
            '<div><label for="a">First name</label> <input id="a"></div>'
            '<div><label for="b">Last name</label> <input id="b"></div>'
            '<div><label for="c">Email</label> <input id="c" type="email"></div></form></body></html>')


def names_token(html: str, token: str) -> str:
    """The page names its board, as a company page embedding Greenhouse's job board script does."""
    return html.replace("</body>", "") + f'<script src="https://boards.greenhouse.io/embed/job_board/js?for={token}"></script>'


@pytest.fixture(scope="module")
async def gh_browser(fixture_server):
    rules = (f"--host-resolver-rules=MAP {GH_HOST} 127.0.0.1:{fixture_server.port} , "
             f"MAP {COMPANY} 127.0.0.1:{fixture_server.port} , "
             "MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1")
    session = BrowserSession(browser_profile=offline_profile(args=[rules]))
    await session.start()
    yield session
    await session.kill()


class GreenhouseBoard:
    """What each board token's embed/job_app page does, for one test."""

    def __init__(self, server, pages) -> None:
        self.server, self.pages, self.markers = server, pages, []

    def page(self, token: str) -> str:
        return f"{self.pages.prefix}/job_app_{token}.html"

    def on_gh(self, name: str, query: str = "") -> str:
        return f"http://{GH_HOST}/{self.pages.prefix}/{name}{query}"

    def has(self, token: str, html: str | None = None) -> None:
        """This token's form page shows `html` (default: this job's Greenhouse form)."""
        self.pages.write(f"job_app_{token}.html", html if html is not None else gh_form(JOB))

    def closed(self, token: str, status: int = 200) -> None:
        """This token's form page redirects to the board's closed-job marker, served with `status`."""
        self.server.respond(token, status, body=f"<!doctype html><title>Jobs</title><h1>Jobs at {token}</h1>"
                                                "<p>The job you are looking for is no longer open.</p>")
        self.markers.append(token)
        self.server.redirect(self.page(token), f"http://{GH_HOST}/{token}?error=true")

    def redirects(self, token: str, to: str) -> None:
        self.server.redirect(self.page(token), to)

    def cleanup(self) -> None:
        for token in self.markers:
            self.server.forget(token)


@pytest.fixture
def gh_board(monkeypatch, fixture_server, pages):
    board = GreenhouseBoard(fixture_server, pages)
    monkeypatch.setattr(boards, "GREENHOUSE_FORM",
                        f"http://{GH_HOST}/{pages.prefix}/job_app_{{token}}.html?for={{token}}&token={{job_id}}")
    yield board
    board.cleanup()


def company(pages, name: str, html: str, status: int = 200, query: str = "") -> str:
    """A wrapper page on the company's own host (no extension needed: typed explicitly)."""
    (pages.dir / name).parent.mkdir(parents=True, exist_ok=True)
    pages.write(name, html, status=status, headers={"Content-Type": "text/html; charset=utf-8"})
    return f"http://{COMPANY}/{pages.prefix}/{name}{query}"


AKAMAI = (FIXTURES / "akamai_access_denied.html").read_text()
GONE_404 = (FIXTURES / "greenhouse_job_not_found.html").read_text()


def route_rows(trace: Trace) -> list[str]:
    return [r["note"] for r in trace.rows if r["note"].startswith("greenhouse")]


# ── the route reaches the job's own form ──

@pytest.mark.browser
@pytest.mark.parametrize("name, html, status, query", [
    ("jobs/senior", AKAMAI, 403, f"?gh_jid={JOB}"),                     # run 83a0efc2's shape
    ("jobs/senior", GONE_404, 404, f"?gh_jid={JOB}"),                   # rule 4 waits for the route
    ("jobs/senior", GONE_404, 410, f"?gh_jid={JOB}"),
    (f"company/careers/departments/job.{JOB}", AKAMAI, 403, ""),        # F3: Rubrik's own URL shape
    (f"jobs/{JOB}", AKAMAI, 403, ""),                                   # F3
])
async def test_a_walled_wrapper_naming_a_board_job_reaches_that_jobs_own_form(
        gh_browser, pages, llm, agent_llm, gh_board, name, html, status, query) -> None:
    gh_board.has("acmeco")
    assert await run_discover(gh_browser, company(pages, name, html, status, query)) is None
    assert (await gh_browser.get_current_page_url()).startswith(gh_board.on_gh("job_app_acmeco.html"))
    assert llm.calls == [] and agent_llm.calls == []


@pytest.mark.browser
@pytest.mark.parametrize("landing, query, html", [
    ("apply.html", "", gh_form(JOB)),                                         # URL without the id; the page names it
    ("job_app.html", f"?for=acmeco&token={JOB}&gh_src=careers", gh_form(None)),  # an added parameter; the URL names it
])
async def test_a_board_form_reached_through_a_benign_redirect_is_used(
        gh_browser, pages, llm, agent_llm, gh_board, landing, query, html) -> None:
    pages.write(landing, html)
    gh_board.redirects("acmeco", gh_board.on_gh(landing, query))
    assert await run_discover(gh_browser, company(pages, "jobs/senior", AKAMAI, 403, f"?gh_jid={JOB}")) is None
    assert (await gh_browser.get_current_page_url()).startswith(gh_board.on_gh(landing))


@pytest.mark.browser
async def test_a_404_marker_means_not_this_board_and_the_next_token_is_tried(
        gh_browser, pages, llm, agent_llm, gh_board) -> None:
    """The page names a board ("wrongco") that doesn't exist (404 marker); the domain's guess is the right one."""
    gh_board.closed("wrongco", 404)
    gh_board.has("acmeco")
    url = company(pages, "jobs/senior", names_token(INERT_403, "wrongco"), 403, f"?gh_jid={JOB}")
    trace = Trace()
    assert await run_discover(gh_browser, url, trace) is None
    assert [n.split(":")[0] for n in route_rows(trace)][:2] == ["greenhouse wrongco", "greenhouse"]


# ── never another job's form ──

@pytest.mark.browser
@pytest.mark.parametrize("why", ["another job's form", "off Greenhouse's host", "no Greenhouse form signature",
                                 "no form at all"])
async def test_a_route_landing_that_cant_be_verified_is_never_used(
        gh_browser, pages, llm, agent_llm, gh_board, fixture_server, why) -> None:
    if why == "another job's form":
        pages.write("other.html", gh_form("9999999"))
        gh_board.redirects("acmeco", gh_board.on_gh("other.html", "?for=acmeco&token=9999999"))
    elif why == "off Greenhouse's host":
        gh_board.redirects("acmeco", pages.write("job_app.html", gh_form(JOB)) + f"?for=acmeco&token={JOB}")
    elif why == "no Greenhouse form signature":
        gh_board.has("acmeco", gh_form(JOB, signature=False))
    else:
        gh_board.has("acmeco", f'<!doctype html><title>Acme</title><main data-job-id="{JOB}">Loading…</main>')
    failed = await run_discover(gh_browser, company(pages, "jobs/senior", AKAMAI, 403, f"?gh_jid={JOB}"))
    assert failed is not None and failed.reason == "blocked", failed
    assert "board route: unverified" in failed.detail
    assert "9999999" not in await gh_browser.get_current_page_url()
    assert llm.calls == [] and agent_llm.calls == []


# ── the closed-job marker during the route (lead's ruling) ──

@pytest.mark.browser
async def test_gh_jid_and_a_page_named_token_on_a_200_marker_is_the_boards_word_job_closed(
        gh_browser, pages, llm, agent_llm, gh_board) -> None:
    """(a) Both halves explicit: the wrapper loads (200, little content) and names its board; the board says gone."""
    gh_board.closed("acmeboard", 200)
    wrapper = names_token('<!doctype html><title>Careers</title><div id="grnhse_app"></div>', "acmeboard")
    failed = await run_discover(gh_browser, company(pages, "jobs/senior", wrapper, 200, f"?gh_jid={JOB}"))
    assert failed is not None and failed.reason == "job_closed", failed
    assert "closed on acmeboard" in failed.detail
    assert llm.calls == [] and agent_llm.calls == []


@pytest.mark.browser
@pytest.mark.parametrize("why, name, html, query, token", [
    ("(b) a guessed token", "jobs/senior", AKAMAI, f"?gh_jid={JOB}", "acmeco"),
    ("(c) a path id, guessed token", f"jobs/{JOB}", AKAMAI, "", "acmeco"),
    ("(c) a path id, page-named token", f"jobs/{JOB}", names_token(INERT_403, "acmeboard"), "", "acmeboard"),
])
async def test_a_200_marker_without_both_halves_explicit_is_no_closure_evidence(
        gh_browser, pages, llm, agent_llm, gh_board, why, name, html, query, token) -> None:
    """A guessed token may be another company's board; a path number may be no Greenhouse id at all. The
    wrapper's own evidence decides (here blocked), and the detail says where the route ended."""
    for t in ("acmeboard", "acmeco"):
        gh_board.closed(t, 200 if t == token else 404)
    failed = await run_discover(gh_browser, company(pages, name, html, 403, query))
    assert failed is not None and failed.reason == "blocked", (why, failed)
    assert f"board route: closed on {token}" in failed.detail, why


@pytest.mark.browser
async def test_a_gh_jid_with_a_page_named_token_on_a_404_marker_is_not_closure(
        gh_browser, pages, llm, agent_llm, gh_board) -> None:
    """404: not this board. Every candidate missing that way is no route at all."""
    gh_board.closed("acmeboard", 404)
    gh_board.closed("acmeco", 404)
    url = company(pages, "jobs/senior", names_token(INERT_403, "acmeboard"), 403, f"?gh_jid={JOB}")
    failed = await run_discover(gh_browser, url)
    assert failed is not None and failed.reason == "blocked", failed
    assert "board route: none" in failed.detail and "board id in URL: yes" in failed.detail


@pytest.mark.browser
async def test_a_wrapper_404_whose_board_route_finds_nothing_ends_job_closed_by_rule_4(
        gh_browser, pages, llm, agent_llm, gh_board) -> None:
    """Accepted residual (lead, 2026-10-21): a guessed token missing on a 404 marker, then the wrapper's own 404."""
    gh_board.closed("acmeco", 404)
    failed = await run_discover(gh_browser, company(pages, "jobs/senior", GONE_404, 404, f"?gh_jid={JOB}"))
    assert failed is not None and failed.reason == "job_closed", failed
    assert "board route: none" in failed.detail


@pytest.mark.browser
async def test_the_route_tries_at_most_four_candidates(gh_browser, pages, llm, agent_llm, gh_board) -> None:
    tokens = [f"board{i}" for i in range(6)]
    for t in tokens + ["acmeco"]:
        gh_board.closed(t, 404)
    html = INERT_403
    for t in tokens:
        html = names_token(html, t)
    trace = Trace()
    failed = await run_discover(gh_browser, company(pages, "jobs/senior", html, 403, f"?gh_jid={JOB}"), trace)
    assert failed is not None and failed.reason == "blocked"
    assert 1 <= len(route_rows(trace)) <= 4, route_rows(trace)


# ── the blocked detail says whether the URL named a board job ──

@pytest.mark.browser
@pytest.mark.parametrize("query, note", [(f"?gh_jid={JOB}", "; board id in URL: yes"), ("", "; board id in URL: no")])
async def test_a_blocked_detail_says_whether_the_url_named_a_board_job(
        gh_browser, pages, llm, agent_llm, gh_board, query, note) -> None:
    gh_board.closed("acmeco", 404)
    failed = await run_discover(gh_browser, company(pages, "jobs/senior", AKAMAI, 403, query))
    assert failed is not None and failed.reason == "blocked", failed
    assert note in failed.detail


# ── the route's pure parts: candidates and verification ──

def page_js(html: str):
    async def run_js(code):
        return html
    return run_js


@pytest.mark.parametrize("url, job_id", [
    ("https://www.rubrik.com/company/careers/departments/job.8274601", "8274601"),
    ("https://www.rubrik.com/company/careers/departments/job.8274601?", "8274601"),
    ("https://careers.acme.com/jobs/8274601", "8274601"),
    ("https://careers.acme.com/jobs/8274601/", "8274601"),
    ("https://careers.acme.com/jobs/8274601#apply", "8274601"),
    ("https://careers.acme.com/jobs/job.1111111?gh_jid=8274601", "8274601"),   # gh_jid wins over the path
    ("https://careers.acme.com/blog/2024/", None),                              # a year, not a job
    ("https://careers.acme.com/jobs/12345", None),                              # 5 digits: too short
    ("https://careers.acme.com/jobs/8274601-senior-engineer", None),            # not bounded by / ? # or end
    ("https://careers.acme.com/jobs/8274601abc", None),
    ("https://careers.acme.com/jobs?page=8274601", None),                       # a query number is no path id
    ("https://careers.acme.com/jobs?gh_jid=123abc", None),                      # malformed gh_jid, no path id
])
def test_greenhouse_job_id_reads_gh_jid_first_then_a_job_shaped_path_number(url, job_id) -> None:
    assert boards.greenhouse_job_id(url) == job_id


async def test_route_candidates_carry_the_urls_job_id_whatever_the_page_names() -> None:
    """Untrusted markup may name any board and link any job; the id always comes from the URL."""
    html = ('<a href="https://job-boards.greenhouse.io/evil/jobs/999999">other</a>'
            '<script src="https://boards.greenhouse.io/embed/job_board/js?for=named"></script>')
    routes = await GREENHOUSE.application_routes(f"https://careers.acmeco.com/jobs?gh_jid={JOB}", page_js(html), False)
    assert routes and {r.job_id for r in routes} == {JOB}
    assert all(f"token={JOB}" in r.url for r in routes)
    assert [r.label for r in routes][:2] == ["evil", "named"] or [r.label for r in routes][:2] == ["named", "evil"]
    assert routes[-1].label == "acmeco" and len(routes) <= 4


@pytest.mark.parametrize("url, html, path_ids, explicit", [
    (f"https://careers.acmeco.com/jobs?gh_jid={JOB}", names_token("", "acmeboard"), False, {"acmeboard": True, "acmeco": False}),
    (f"https://careers.acmeco.com/jobs?gh_jid={JOB}", "", False, {"acmeco": False}),
    (f"https://careers.acmeco.com/jobs/{JOB}", names_token("", "acmeboard"), True, {"acmeboard": False, "acmeco": False}),
])
async def test_a_route_is_explicit_only_with_gh_jid_and_a_page_named_token(url, html, path_ids, explicit) -> None:
    routes = await GREENHOUSE.application_routes(url, page_js(html), path_ids)
    assert {r.label: r.explicit for r in routes} == explicit


@pytest.mark.parametrize("url, path_ids", [
    (f"https://job-boards.greenhouse.io/acme/jobs/{JOB}?gh_jid={JOB}", True),  # already on Greenhouse
    ("https://careers.acmeco.com/jobs/senior", True),                          # no board id
    (f"https://careers.acmeco.com/jobs/{JOB}", False),                         # a path id, but not a dead end
    ("https://careers.acmeco.com/jobs?gh_jid=123abc", True),                   # malformed gh_jid
    ("https://careers.acmeco.com/blog/2024/", True),
])
async def test_no_route_without_a_wrapper_naming_a_board_job(url, path_ids) -> None:
    assert await GREENHOUSE.application_routes(url, page_js(""), path_ids) == []


FORM_HTML = f'<form id="application-form"><input type="hidden" name="job_id" value="{JOB}"></form>'


@pytest.mark.parametrize("landed, html, ok", [
    (f"https://job-boards.greenhouse.io/embed/job_app?for=acme&token={JOB}", FORM_HTML, True),
    (f"https://boards.greenhouse.io/embed/job_app?for=acme&token={JOB}", FORM_HTML, True),
    (f"https://job-boards.eu.greenhouse.io/embed/job_app?for=acme&token={JOB}", FORM_HTML, True),
    ("https://job-boards.greenhouse.io/acme/apply", FORM_HTML, True),                        # id in the page
    (f"https://job-boards.greenhouse.io/acme/jobs/{JOB}", '<div id="grnhse_app"></div>', True),  # id in the URL
    (f"https://job-boards.greenhouse.io.evil.com/embed/job_app?token={JOB}", FORM_HTML, False),  # host lookalike
    (f"https://evil.com/?u=https://job-boards.greenhouse.io/&token={JOB}", FORM_HTML, False),
    (f"https://careers.acme.com/apply?token={JOB}", FORM_HTML, False),                       # the company's host
    (f"https://job-boards.greenhouse.io/embed/job_app?token={JOB}", "<form id='x'></form>", False),  # no signature
    ("https://job-boards.greenhouse.io/acme/apply", '<form id="application-form"></form>', False),   # no id anywhere
    ("https://job-boards.greenhouse.io/embed/job_app?token=9999999",
     '<form id="application-form"><input value="9999999"></form>', False),                   # another job
    (f"https://job-boards.greenhouse.io/embed/job_app?token={JOB}1", '<form id="application-form"></form>', False),
    ("https://job-boards.greenhouse.io/acme/apply", f'<form id="application-form">{JOB}1</form>', False),  # 20000031
    ("https://job-boards.greenhouse.io/acme/apply", f'<form id="application-form">1{JOB}</form>', False),
])
def test_route_verification_needs_the_boards_host_this_jobs_id_and_the_form_signature(landed, html, ok) -> None:
    assert GREENHOUSE.route_verified(landed, html, JOB) is ok


@pytest.mark.browser
@pytest.mark.parametrize("html", [gh_form(None, signature=False), NO_ROUTE_JOB_PAGE := (
    '<!doctype html><title>Engineer</title><main><h1>Engineer</h1><a href="#">Teams</a> <a href="#">Benefits</a> '
    '<a href="#">Blog</a> <a href="#">FAQ</a> <a href="#">Home</a> <a href="#">About</a></main>')])
async def test_a_path_number_on_a_live_page_never_starts_the_board_route(
        gh_browser, pages, llm, agent_llm, gh_board, html) -> None:
    """F3 path ids are read only on a dead end (rules 2-4): a company page that loads is the company's."""
    gh_board.has("acmeco")
    llm.on("driver", "NavDecision", handler=lambda call: {"reasoning": "none", "state": "unknown"})
    agent_llm.done(data={"state": "other", "explanation": "No form."})
    trace = Trace()
    await run_discover(gh_browser, company(pages, f"jobs/{JOB}", html, 200), trace)
    assert route_rows(trace) == []
    assert not (await gh_browser.get_current_page_url()).startswith(f"http://{GH_HOST}/")
