"""
Reach the application form and map its fields: deterministic code first, models as fallbacks.

Discover (get to the application form):
  1. read_form_schema finds a form already; else click a visible "Apply" button  -- code
  2. one-shot: the page's indexed elements -> one element to click (≤2 rounds)  -- driver
  3. short navigation agent                                                     -- driver (+ escalation)
  Every tier's result is checked in code (read_form_schema) before it's accepted; anything short of
  a visible form — even a model's "job closed" — falls through to the next tier.
  Before the tiers: a wrapper page is swapped for the board's own form when the board can confirm
  it (Greenhouse ?gh_jid=), and evidence in code ends discover with no model asked: an anti-bot
  interstitial or a refusing HTTP status (blocked), the board's closed-job page (job_closed).
  The same check runs after every click that led nowhere.
Map (list every field, each tagged with a data-ja-field key for filling later):
  1. read_form_schema                                      -- code
  2. one-shot: cleaned form HTML -> JSON with a selector per field; every selector is
     verified against the live page before it's trusted   -- driver
  3. form-mapping agent, same output and verification      -- driver (+ escalation)
  Custom dropdown options are read in code; a dropdown whose options can't be read
  gets a tiny agent task of its own.

Job info (title/company/location/description) comes from schema.org JobPosting JSON-LD when
the page has it, else the page's main text is handed to the metadata model unparsed.
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass
from html import unescape
from typing import Literal

from browser_use.browser import BrowserSession
from browser_use.browser.events import ClickElementEvent, SwitchTabEvent
from browser_use.llm.messages import SystemMessage, UserMessage
from loguru import logger
from pydantic import BaseModel
from temporalio import activity

from yahbah.agent.boards import (
    GENERIC,
    BoardProfile,
    BoardRoute,
    board_id_in_url,
    board_routes,
    detect_board,
    embedded_form_url,
    record_blocked,
    with_notes,
)
from yahbah.agent.fill import capture_empty_values
from yahbah.agent.session import SessionRegistry, SessionSuperseded
from yahbah.agent.llm import chat_model, make_crew, run_agent
from yahbah.agent.tools import get_form_schema, node_for_key, read_dropdown_by_key, run_js
from yahbah.config import load_prompts_config
from yahbah.llm.client import LLMError, OllamaClient

# A visible link/button whose whole text is an apply call-to-action. Tagged so we can find it again.
# (The one tolerated phrase list: a miss only costs time, the navigation agent is behind it.)
FIND_APPLY_JS = r"""
(() => {
  const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const re = /^\s*(apply|apply now|apply for this (job|position|role)|apply here|start (your )?application|i'?m interested)\s*$/i;
  const el = [...document.querySelectorAll('a, button, [role=button], input[type=submit], input[type=button]')]
    .find((e) => vis(e) && re.test(e.innerText || e.value || e.getAttribute('aria-label') || ''));
  if (!el) return null;
  el.dataset.jaField = 'apply-cta';
  el.scrollIntoView({ block: 'center' });
  return (el.innerText || el.value || '').trim();
})()
"""

# Job info from structured data only: schema.org JobPosting JSON-LD, which many job boards embed.
JOB_POSTING_JS = r"""
(() => {
  const walk = (x) => Array.isArray(x) ? x.flatMap(walk) : x && typeof x === 'object' ? [x, ...walk(x['@graph'] || [])] : [];
  const name = (v) => typeof v === 'string' ? v : (v && v.name) || '';
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const jp = walk(JSON.parse(s.textContent)).find((o) => [].concat(o['@type']).includes('JobPosting'));
      if (!jp) continue;
      const loc = [].concat(jp.jobLocation || []).map((l) => {
        const a = (l && l.address) || {};
        return [a.addressLocality, a.addressRegion, name(a.addressCountry)].filter(Boolean).join(', ');
      }).filter(Boolean).join(' | ');
      const desc = jp.description ? new DOMParser().parseFromString(jp.description, 'text/html').body.textContent : '';
      return JSON.stringify({
        job_title: jp.title || '', company: name(jp.hiringOrganization),
        location: loc || (jp.jobLocationType === 'TELECOMMUTE' ? 'Remote' : ''),
        posted_date: String(jp.datePosted || '').slice(0, 10), url: jp.url || '', description: desc,
      });
    } catch {}
  }
  return null;
})()
"""

# The page's main text, handed to the metadata model as-is (we don't interpret it in code).
PAGE_TEXT_JS = r"""
(() => {
  const root = document.querySelector('main, [role=main], article') || document.body;
  return (root.innerText || '').replace(/\n{3,}/g, '\n\n').trim().slice(0, 20000);
})()
"""

# The form (or the page, if there's no <form>) stripped to what's needed to map fields:
# no scripts/styles/svg, only form-relevant attributes, long text blocks (job description) truncated.
CLEAN_FORM_HTML_JS = r"""
(() => {
  const forms = [...document.querySelectorAll('form')];
  const count = (el) => el.querySelectorAll('input, select, textarea, [role=combobox]').length;
  const root = (forms.sort((a, b) => count(b) - count(a))[0]) || document.body;
  const clone = root.cloneNode(true);
  clone.querySelectorAll('script, style, svg, noscript, img, iframe, video, canvas, link, meta').forEach((e) => e.remove());
  const KEEP = /^(id|name|type|for|role|required|placeholder|value|multiple|accept|checked|selected|disabled|tabindex|aria-.*|data-ja-field|data-testid)$/;
  for (const el of clone.querySelectorAll('*')) {
    for (const a of [...el.attributes]) if (!KEEP.test(a.name)) el.removeAttribute(a.name);
  }
  const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode()); ) {
    const t = n.textContent.replace(/\s+/g, ' ');
    n.textContent = t.length > 300 ? t.slice(0, 300) + '…' : t;
  }
  return clone.outerHTML.replace(/>\s+</g, '><').replace(/<(div|span)>(<\/\1>)/g, '');
})()
"""

COUNT_SELECTORS_JS = r"""
((sels) => JSON.stringify(sels.map((s) => { try { return document.querySelectorAll(s).length; } catch { return -1; } })))
"""

TAG_SELECTOR_JS = r"""
((sel, key) => { const el = document.querySelector(sel); if (el) el.dataset.jaField = key; return !!el; })
"""


class StageFailed(Exception):
    """A stage could not complete. reason is a short code (job_closed, login_wall, captcha, blocked,
    form_not_found, form_unmapped, needs_answer, fill_failed, submit_unverified, ...)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class Trace:
    """Per-run stage log: which method (code / model) each stage needed and how long it took."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, stage: str, method: str, started: float, note: str = "") -> None:
        secs = round(time.time() - started, 1)
        self.rows.append({"stage": stage, "method": method, "secs": secs, "note": note})
        logger.info(f"[{stage}] {method}: {note} ({secs}s)")
        beat(f"{stage}: {note}")


def beat(note: str) -> None:
    """Heartbeat when running inside a Temporal activity (no-op in scripts)."""
    if activity.in_activity():
        activity.heartbeat(note)


async def settle(secs: float = 2.0) -> None:
    await asyncio.sleep(secs)


# ── Dead ends: decided by evidence in code (contract §2, discover-stage classification) ─────────
# Anti-bot interstitials, by vendor: markers of the vendor's block or challenge page itself, not of
# the ordinary pages it protects (Cloudflare's challenge-platform script, DataDome's tags.js and the
# reCAPTCHA v3 badge are on normal pages too). Every marker is anchored in markup (the title, an id or
# class, an iframe/script src, the vendor's inline config), never in prose: a live job page quoting a
# WAF page is not one. Matched against the HTML, entities decoded.
BLOCK_SIGNATURES: dict[str, str] = {
    "akamai": r"<title>\s*Access Denied\s*</title>[\s\S]*(Reference\s*#\d+\.[0-9a-f]+\.|errors\.edgesuite\.net/)",
    "cloudflare": r"<title>\s*(Just a moment\.\.\.|Attention Required! \| Cloudflare)\s*</title>"
                  r"|window\._cf_chl_opt|id=\"challenge-(running|form|error-text)\"|(id|class)=\"cf-error-details",
    "perimeterx": r"id=\"px-captcha\"|<title>\s*Access to this page has been denied",
    "datadome": r"src=\"https?://(geo|ct)\.captcha-delivery\.com/",
    "imperva": r"src=\"[^\"]*/_Incapsula_Resource\?",
}
# A status alone decides, whatever the page says (a 403 page titled "Job not found" is still a 403),
# but only on an inert page (next to nothing to click): single-page apps on static hosting answer
# deep links with a 403/404 and then render normally. INERT_MAX_CONTROLS is named in the contract.
BLOCK_STATUSES = {401, 403, 407, 429}
GONE_STATUSES = {404, 410}
INERT_MAX_CONTROLS = 5
# How long a status-only verdict must keep holding before it's trusted (a slow app renders late).
STATUS_RECHECK_SECS = 3.0
# Visible human-verification widgets on the site's own page (not the invisible reCAPTCHA v3 badge).
CAPTCHA_WIDGETS = ", ".join([
    'iframe[src*="/recaptcha/api2/anchor"]:not([src*="size=invisible"])',
    'iframe[src*="/recaptcha/enterprise/anchor"]:not([src*="size=invisible"])',
    'iframe[src*="hcaptcha.com"][src*="checkbox"]', ".h-captcha iframe",
    'iframe[src*="challenges.cloudflare.com"]', ".cf-turnstile",
])
# Advisory only: decided after the tiers fail, since a guest-apply path may exist.
LATE_REASONS = {"captcha", "login_wall"}

# The evidence (PageEvidence): the main document's HTTP status (Navigation Timing; 0 for file:// and
# error pages), the real document.title, rendered text and HTML (heads of them), visible controls.
PAGE_EVIDENCE_JS = r"""
(() => {
  const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const count = (sel) => [...document.querySelectorAll(sel)].filter(vis).length;
  return JSON.stringify({
    url: location.href,
    status: (performance.getEntriesByType('navigation')[0] || {}).responseStatus || null,
    title: document.title,
    text: ((document.body && document.body.innerText) || '').slice(0, 5000),
    html: document.documentElement.outerHTML.slice(0, 200000),
    controls: count('a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link]'),
    captcha_widgets: count(__CAPTCHA__),
    password_fields: count('input[type=password]'),
  });
})()
""".replace("__CAPTCHA__", json.dumps(CAPTCHA_WIDGETS))


@dataclass(frozen=True)
class PageEvidence:
    url: str
    status: int | None  # the main document's HTTP status, None when unknown
    title: str
    text: str  # rendered (entities decoded), head of it
    html: str  # head of it
    controls: int  # visible clickable/fillable elements
    captcha_widgets: int = 0  # visible CAPTCHA_WIDGETS
    password_fields: int = 0  # visible password inputs


@dataclass(frozen=True)
class PageVerdict:
    reason: str  # a contract §2 failure reason
    signal: str  # what decided it: the vendor (akamai, ...), http_<status>, greenhouse:closed, ...
    detail: str  # for the user and triage: the signal, the status and the URL
    url: str = ""  # the page judged


def classify_page(ev: PageEvidence, board: BoardProfile) -> PageVerdict | None:
    """Whether this page is a dead end, from evidence alone (no model, no session, no page words);
    None when nothing here settles it. Precedence as in contract §2 (rule 1, a visible application
    form, is the caller's: see raise_if_dead_end)."""
    def verdict(reason: str, signal: str) -> PageVerdict:
        return PageVerdict(reason, signal, f"{signal} (HTTP {ev.status or '?'}) at {ev.url}", ev.url)

    inert = ev.controls <= INERT_MAX_CONTROLS
    page = f"<title>{ev.title}</title>\n{unescape(ev.html)}"
    for vendor, pattern in BLOCK_SIGNATURES.items():
        if re.search(pattern, page, re.I):
            return verdict("blocked", vendor)                         # 2
    if ev.status in BLOCK_STATUSES and inert:
        return verdict("blocked", f"http_{ev.status}")                # 3
    if ev.status in GONE_STATUSES and inert:
        return verdict("job_closed", f"http_{ev.status}")             # 4
    if board.closed_at(ev.url):
        return verdict("job_closed", f"{board.name}:closed")          # 5
    if ev.captcha_widgets:
        return verdict("captcha", "captcha_widget")                   # 6
    if ev.password_fields:
        return verdict("login_wall", "password_field")                # 7
    if ev.url.startswith("chrome-error://"):
        return verdict("browser_error", "net_error")                  # 8
    if ev.status and ev.status >= 500:
        return verdict("browser_error", f"http_{ev.status}")
    return None


async def read_page_evidence(session: BrowserSession) -> PageEvidence:
    return PageEvidence(**json.loads(await run_js(session, PAGE_EVIDENCE_JS)))


async def page_verdict(session: BrowserSession, board: BoardProfile) -> PageVerdict | None:
    try:
        ev = await read_page_evidence(session)
    except Exception as exc:  # a page mid-navigation; the next check sees the settled one
        logger.warning(f"[discover] reading page evidence failed: {exc}")
        return None
    return classify_page(ev, board)


async def raise_if_dead_end(session: BrowserSession, board: BoardProfile, late: bool = False,
                            note: str = "") -> None:
    """Stop on evidence in code that no form is reachable here (contract §2)."""
    if verdict := await dead_end_verdict(session, board, late):
        raise dead_end(verdict, board, note)


async def dead_end_verdict(session: BrowserSession, board: BoardProfile, late: bool = False) -> PageVerdict | None:
    """The verdict that ends discover here, or None. Before and between the tiers (late=False) only
    the reasons that need no guest-apply check (blocked, job_closed, browser_error); after them
    (late=True) captcha and login_wall too. A visible application form always wins, and a status-only
    verdict must keep holding while a slow app gets time to render."""
    verdict = await page_verdict(session, board)
    if verdict and verdict.signal.startswith("http_") and verdict.reason != "browser_error":
        deadline = time.monotonic() + STATUS_RECHECK_SECS
        while verdict and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            verdict = await page_verdict(session, board)
    if not verdict or (verdict.reason in LATE_REASONS and not late):
        return None
    if looks_like_form(await get_form_schema(session)):
        return None
    return verdict


def dead_end(verdict: PageVerdict, board: BoardProfile, note: str = "") -> StageFailed:
    """The StageFailed for a verdict. A blocked one also says whether its URL named a board job, in the
    detail and in _observations.jsonl (how often a walled page names no board job)."""
    if verdict.reason != "blocked":
        return StageFailed(verdict.reason, verdict.detail + note)
    url = verdict.url
    try:
        record_blocked(url, board, verdict.signal)
    except OSError as exc:
        logger.warning(f"[discover] could not record the blocked observation: {exc}")
    return StageFailed("blocked", f"{verdict.detail}{note}; board id in URL: {'yes' if board_id_in_url(url) else 'no'}")


def looks_like_form(fields: list[dict]) -> bool:
    """An application form, not a search box or newsletter signup."""
    fillable = [f for f in fields if f["type"] not in ("checkbox", "radio")]
    return len(fillable) >= 3 or any(f["type"] == "file" for f in fields)


def schema_trustworthy(fields: list[dict]) -> bool:
    return looks_like_form(fields) and all(f["label"] for f in fields)


# ── Job info ──────────────────────────────────────────────────────────────────

async def read_job_info(session: BrowserSession) -> dict:
    """Title/company/location/posted date/description: JSON-LD if present, else raw page text."""
    if found := await run_js(session, JOB_POSTING_JS):
        info = json.loads(found)
        info["source"] = "json-ld"
        if len(info.get("description") or "") < 200:
            info["description"] = await run_js(session, PAGE_TEXT_JS) or ""
        return info
    return {"source": "page-text", "description": await run_js(session, PAGE_TEXT_JS) or ""}


# ── Discover ──────────────────────────────────────────────────────────────────

class NavOutcome(BaseModel):
    state: Literal["form_visible", "job_closed", "login_required", "captcha", "other"]
    explanation: str


@dataclass
class DiscoverResult:
    board: BoardProfile
    page_url: str
    job: dict
    headed: bool = False  # the run continued in the headed fallback browser (contract §2)


async def _follow_embed(session: BrowserSession, trace: Trace) -> None:
    """A company page embedding a known board's form in an iframe: open the form itself."""
    if embedded := await embedded_form_url(lambda c: run_js(session, c)):
        t = time.time()
        await session.navigate_to(embedded[1])
        await settle(3)
        trace.add("discover", "code", t, f"opened embedded {embedded[0].name} form {embedded[1][:80]}")


async def _detect(session: BrowserSession) -> BoardProfile:
    return await detect_board(lambda c: run_js(session, c), await session.get_current_page_url())


async def click_node(session: BrowserSession, node) -> None:
    """A real click; follows the click into a new tab if it opened one (ATS links often do)."""
    tabs_before = len(await session.get_tabs())
    ev = session.event_bus.dispatch(ClickElementEvent(node=node))
    await ev
    await ev.event_result(raise_if_any=True, raise_if_none=False)
    await settle(3)
    if len(await session.get_tabs()) > tabs_before:
        await session.event_bus.dispatch(SwitchTabEvent())
        await settle(2)


async def click_apply(session: BrowserSession, board: BoardProfile) -> str | None:
    text = await run_js(session, board.apply_button_js or FIND_APPLY_JS)
    if not text:
        return None
    node = await node_for_key(session, "apply-cta")
    if node is None:
        return None
    await click_node(session, node)
    return text


class NavDecision(BaseModel):
    reasoning: str  # first, so the model reasons before committing to a decision
    state: Literal["form_visible", "click", "job_closed", "login_required", "captcha", "unknown"]
    element_index: int | None = None


async def oneshot_navigate(session: BrowserSession, board: BoardProfile, trace: Trace, rounds: int = 2) -> bool:
    """One structured driver call per round: the page's indexed interactive elements -> the one element
    to click (an Apply button, an Application tab, a cookie banner in the way). True once the form is
    there; the model's own claims (form visible, job closed, ...) are never trusted unchecked."""
    for _ in range(rounds):
        t = time.time()
        state = await session.get_browser_state_summary(include_screenshot=False)
        selector_map = state.dom_state.selector_map
        try:
            decision = await OllamaClient("driver").generate_structured(
                system_prompt=with_notes(load_prompts_config()["prompts"]["oneshot_discover"], board),
                user_prompt=(f"URL: {state.url}\nTITLE: {state.title}\n\nINTERACTIVE ELEMENTS:\n"
                             f"{state.dom_state.llm_representation()[:40_000]}"),
                response_model=NavDecision,
            )
        except LLMError as exc:
            trace.add("discover", "model", t, f"one-shot failed: {exc}")
            return False
        if decision.state != "click" or decision.element_index not in selector_map:
            trace.add("discover", "model", t, f"one-shot: {decision.state} — {decision.reasoning[:150]}")
            return False
        await click_node(session, selector_map[decision.element_index])
        await _follow_embed(session, trace)
        if looks_like_form(fields := await get_form_schema(session)):
            trace.add("discover", "model", t, f"one-shot: clicked [{decision.element_index}], "
                                              f"form found ({len(fields)} fields)")
            return True
        trace.add("discover", "model", t, f"one-shot: clicked [{decision.element_index}], no form yet")
        await raise_if_dead_end(session, await _detect(session))
    return False


async def _open_board_route(session: BrowserSession, routes: list[tuple[BoardProfile, BoardRoute]], trace: Trace
                           ) -> tuple[BoardProfile | None, str]:
    """Open each candidate board form in turn: (its board, "") for the first verified one, else
    (None, the detail note). A closed-job page at an explicit candidate ends the run job_closed."""
    closed: list[str] = []  # tokens whose real board answered "no such job" (a 200 closed-job page)
    unverified = False
    for board, route in routes:
        t = time.time()
        try:
            await session.navigate_to(route.url)
            await settle(3)
            ev = await read_page_evidence(session)
        except Exception as exc:  # a load that failed or never settled is just no route
            trace.add("discover", "code", t, f"{board.name} {route.label}: unreadable ({exc})")
            continue
        if board.closed_at(ev.url):
            trace.add("discover", "code", t, f"{board.name} {route.label}: closed-job page (HTTP {ev.status})")
            if ev.status == 200:  # a real board without this job (404: not this board)
                if route.explicit:
                    raise StageFailed("job_closed", f"{board.name}:closed (HTTP 200) at {ev.url}; "
                                                    f"board route: closed on {route.label}")
                closed.append(route.label)
            continue
        if board.route_verified(ev.url, ev.html, route.job_id) and looks_like_form(await get_form_schema(session)):
            trace.add("discover", "code", t, f"{board.name}: opened its own form {ev.url[:100]}")
            return board, ""
        trace.add("discover", "code", t, f"{board.name} {route.label}: {ev.url[:100]} is not job {route.job_id}'s form")
        unverified = True
    outcome = f"closed on {', '.join(closed)}" if closed else "unverified" if unverified else "none"
    return None, f"; board route: {outcome}"


async def _headed_fallback(session: BrowserSession, url: str, verdict: PageVerdict, board: BoardProfile,
                           note: str, trace: Trace) -> BrowserSession:
    """The run's browser swapped for a headed one on the same URL (one attempt, no retry). Raises
    blocked ("headed: unavailable") when no headed browser can be had."""
    t = time.time()
    try:
        headed = await SessionRegistry.instance().reopen_headed(session, url)
    except SessionSuperseded:
        raise
    except Exception as exc:  # no GUI session, a locked screen, the worker under launchd, not a run's browser
        trace.add("discover", "code", t, f"headed fallback unavailable ({type(exc).__name__}: {exc})"[:300])
        raise dead_end(verdict, board, note + "; headed: unavailable") from exc
    trace.add("discover", "code", t, f"headed fallback: reopened {url[:100]} headed "
                                     f"(headless was {verdict.reason}: {verdict.signal})")
    return headed


async def discover(session: BrowserSession, trace: Trace, *, headed_fallback: bool = False) -> DiscoverResult:
    """From the job URL's page to a page showing the application form. Raises StageFailed.
    headed_fallback: a page that walls off this (headless) browser, with no board route around it, is
    reopened once in a headed browser, which the run then keeps (contract §2); result.headed says so."""
    headed = False
    # Job info first: the page we were given usually holds the description; the form may not.
    # A block page holds none, so then it's read from wherever the form turns up.
    job = {} if await page_verdict(session, GENERIC) else await read_job_info(session)
    await _follow_embed(session, trace)
    board = await _detect(session)

    # Board route first (contract §2): a wrapper naming a board's job is swapped for the board's own
    # form before the wrapper's evidence (rules 2-4) decides anything.
    url = await session.get_current_page_url()
    verdict = await page_verdict(session, board)
    walled = bool(verdict and (verdict.reason == "blocked" or verdict.signal in ("http_404", "http_410")))
    route_note = ""
    if routes := await board_routes(board, url, lambda c: run_js(session, c), dead_end=walled):
        route_board, route_note = await _open_board_route(session, routes, trace)
        if route_board:
            board = route_board
        else:  # back to the job's own page (never left on a candidate), judged on its live evidence
            await session.navigate_to(url)
            await settle(3)
            await _follow_embed(session, trace)
            board = await _detect(session)

    if verdict := await dead_end_verdict(session, board):
        if verdict.reason != "blocked" or not headed_fallback:
            raise dead_end(verdict, board, route_note)
        session = await _headed_fallback(session, url, verdict, board, route_note, trace)
        headed = True
        await _follow_embed(session, trace)
        board = await _detect(session)
        if verdict := await dead_end_verdict(session, board):
            raise dead_end(verdict, board, route_note + f"; headed: {'blocked' if verdict.reason == 'blocked' else 'yes'}")
        route_note += "; headed: yes"  # every later failure says the run was headed
    if not job:
        job = await read_job_info(session)

    t = time.time()
    if looks_like_form(fields := await get_form_schema(session)):
        trace.add("discover", "code", t, f"form already on page ({len(fields)} fields)")
        return DiscoverResult(board, await session.get_current_page_url(), job, headed)

    t = time.time()
    clicked = await click_apply(session, board)
    await _follow_embed(session, trace)
    if looks_like_form(fields := await get_form_schema(session)):
        trace.add("discover", "code", t, f"clicked {clicked!r}, form found ({len(fields)} fields)")
        return DiscoverResult(await _detect(session), await session.get_current_page_url(), job, headed)
    trace.add("discover", "code", t, f"clicked {clicked!r}, no form" if clicked else "no apply button found")
    if clicked:
        await raise_if_dead_end(session, await _detect(session))

    if await oneshot_navigate(session, board, trace):
        return DiscoverResult(await _detect(session), await session.get_current_page_url(), job, headed)

    t = time.time()
    history = await run_agent(
        with_notes(load_prompts_config()["prompts"]["agent_discover_task"], board),
        make_crew(), session, max_steps=6, output=NavOutcome, mode="navigate",
    )
    outcome: NavOutcome | None = history.structured_output
    await _follow_embed(session, trace)
    fields = await get_form_schema(session)
    trace.add("discover", "model", t, f"navigation agent -> {outcome.state if outcome else 'no result'}, "
                                      f"{len(fields)} fields")
    if looks_like_form(fields):
        # Navigation may have landed on a different board (e.g. company site -> Greenhouse).
        return DiscoverResult(await _detect(session), await session.get_current_page_url(), job, headed)
    # Evidence in code decides; the agent's answer (job closed, login, captcha, ...) is only advice.
    await raise_if_dead_end(session, await _detect(session), late=True, note=route_note)
    advice = f"{outcome.state}: {outcome.explanation}" if outcome else "navigation agent gave no result"
    raise StageFailed("form_not_found", f"no application form reached{route_note}; model advice: {advice}")


# ── Map form ──────────────────────────────────────────────────────────────────

class MappedField(BaseModel):
    label: str
    type: str  # text, email, tel, number, url, date, textarea, select, combobox, radio, checkbox, file
    required: bool
    options: list[str] | None = None
    selector: str


class MappedForm(BaseModel):
    fields: list[MappedField]


class DropdownOptions(BaseModel):
    options: list[str]  # as shown; for more than 15, only the first 15
    total: int


async def _verified_fields(session: BrowserSession, form: MappedForm | None, prefix: str) -> list[dict] | None:
    """Keep only fields whose selector matches exactly one element, tag those with keys.
    None if too many selectors miss (the model was guessing)."""
    if not form or not form.fields:
        return None
    counts = json.loads(await run_js(session, f"{COUNT_SELECTORS_JS}({json.dumps([f.selector for f in form.fields])})"))
    ok = [f for f, n in zip(form.fields, counts) if n == 1]
    bad = [f"{f.label!r} -> {f.selector!r} ({n} matches)" for f, n in zip(form.fields, counts) if n != 1]
    if bad:
        logger.info("[map] selectors that did not match exactly one element:\n  " + "\n  ".join(bad))
    fields = [{"label": f.label, "type": f.type, "required": f.required, "options": f.options or []} for f in ok]
    if len(ok) < 0.8 * len(form.fields) or not looks_like_form(fields):
        return None
    for i, (f, d) in enumerate(zip(ok, fields)):
        d["key"] = f"{prefix}-{i}"
        await run_js(session, f"{TAG_SELECTOR_JS}({json.dumps(f.selector)}, {json.dumps(d['key'])})")
    return fields


async def oneshot_map(session: BrowserSession, board: BoardProfile) -> list[dict] | None:
    html = (await run_js(session, CLEAN_FORM_HTML_JS))[:120_000]  # ~30k tokens
    llm = chat_model("driver")
    try:
        res = await llm.ainvoke(
            [SystemMessage(content=with_notes(load_prompts_config()["prompts"]["oneshot_form_map"], board)),
             UserMessage(content=f"URL: {await session.get_current_page_url()}\n\n{html}")],
            output_format=MappedForm,
        )
    except Exception as exc:  # invalid JSON / schema mismatch from the model
        logger.warning(f"[map] one-shot call failed: {exc}")
        return None
    return await _verified_fields(session, res.completion, "os")


async def read_dropdowns(session: BrowserSession, fields: list[dict], board: BoardProfile, trace: Trace) -> None:
    """Fill in options for custom dropdowns: in code, else a tiny agent per dropdown."""
    combos = [f for f in fields if f["type"] == "combobox" and not f.get("options")]
    if not combos:
        return
    t = time.time()
    failed = []
    for f in combos:
        data = await read_dropdown_by_key(session, f["key"])
        if data["total"]:
            f["options"], f["options_total"] = data["options"], data["total"]
        else:
            failed.append(f)
    trace.add("dropdowns", "code", t, f"{len(combos) - len(failed)}/{len(combos)} read")
    for f in failed:
        t = time.time()
        task = load_prompts_config()["prompts"]["agent_dropdown_task"].replace("{label}", f["label"])
        h = await run_agent(with_notes(task, board), make_crew(), session, max_steps=4, output=DropdownOptions)
        got: DropdownOptions | None = h.structured_output
        if got:
            f["options"], f["options_total"] = got.options, got.total
        trace.add("dropdowns", "model", t, f"{f['label']!r}: {f.get('options')}")


def _clean_schema_fields(fields: list[dict]) -> list[dict]:
    return [
        {"key": f["key"], "label": f["label"], "type": f["type"], "required": bool(f["required"]),
         "options": f.get("options") or [], "options_total": f.get("options_total"),
         "autocomplete": f.get("autocomplete"), "description": f.get("description") or "",
         "section": f.get("section") or ""}
        for f in fields
    ]


async def map_form(
    session: BrowserSession,
    board: BoardProfile,
    trace: Trace,
    force_tier: Literal["schema", "oneshot", "agent"] = "schema",
) -> list[dict]:
    """Every field of the form, each with a data-ja-field key. Raises StageFailed."""
    fields: list[dict] | None = None

    if force_tier == "schema":
        t = time.time()
        schema = await get_form_schema(session)
        if schema_trustworthy(schema):
            fields = _clean_schema_fields(schema)
            trace.add("map", "code", t, f"read_form_schema: {len(fields)} fields")
        else:
            trace.add("map", "code", t, f"read_form_schema not trustworthy: {json.dumps(schema)[:200]}")

    if fields is None and force_tier in ("schema", "oneshot"):
        t = time.time()
        fields = await oneshot_map(session, board)
        trace.add("map", "model", t, f"one-shot: {len(fields)} fields, selectors verified" if fields
                  else "one-shot: rejected")

    if fields is None:
        t = time.time()
        task = load_prompts_config()["prompts"]["agent_map_form_task"].replace(
            "{url}", await session.get_current_page_url())
        history = await run_agent(with_notes(task, board), make_crew(), session, max_steps=15, output=MappedForm)
        fields = await _verified_fields(session, history.structured_output, "ag")
        trace.add("map", "model", t, f"form-mapping agent: {len(fields) if fields else 0} verified fields")

    if not fields:
        raise StageFailed("form_unmapped", "no tier produced a trustworthy field list")

    await read_dropdowns(session, fields, board, trace)
    await capture_empty_values(session, fields)
    return fields
