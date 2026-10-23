"""
Per-job-board knowledge, kept separate so learning one board can't silently change another.

A BoardProfile holds:
- detection: URL patterns, plus a DOM check for boards served on company domains
- embeds: iframe URLs that hold the real form (open those directly; our readers see the top frame),
  the board's own form URL for a company page wrapping it, and where the board sends closed jobs
- stage hooks: optional board-specific JS for each pipeline stage; None means the generic,
  standards-based code in discover.py / fill.py / submit.py is used
- model_notes: what we learned about the board, added to every model prompt and agent task on it

The generic code (ARIA labels, groups, combobox keyboard handling, ...) stays board-agnostic;
anything only true for one board goes here. Fixtures (test/yahbah/fixtures) check each board's
known pages, so a change made for one board that breaks another shows up immediately.

Every run appends to artifacts/_observations.jsonl (board, which tier succeeded at each stage),
which shows when an unknown board is common enough to deserve its own profile.
"""
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

from yahbah.config import settings

RunJs = Callable[[str], Awaitable[object]]


@dataclass(frozen=True)
class BoardRoute:
    """A candidate URL of the board's own form for a job named on someone else's page."""
    url: str
    job_id: str
    label: str  # what the candidate rests on (Greenhouse: the board token), for traces and details
    # Both halves named outright (Greenhouse: id from gh_jid, token named by the page), so a closed-job
    # page at this URL is the board's own word that the job is gone.
    explicit: bool


@dataclass
class BoardProfile:
    name: str

    # ── Detection ────────────────────────────────────────────────────────────
    url_patterns: list[str] = field(default_factory=list)  # regexes matched against the page URL
    dom_signature_js: str | None = None  # JS expression returning true on this board's pages

    # ── Open ─────────────────────────────────────────────────────────────────
    # Iframe src patterns: if a company page embeds this board's form, open the iframe URL directly.
    embed_patterns: list[str] = field(default_factory=list)
    # Candidate URLs of the board's own form for a job named on another page (its URL, its JS; with
    # path_ids, also a job-id-shaped number in the URL path), most likely first, at most 4. Discover
    # opens them in turn and uses the first that route_verified accepts.
    application_routes: Callable[[str, RunJs, bool], Awaitable[list[BoardRoute]]] | None = None
    # (landed URL, page HTML, job id) -> this is the board's form for that job (pure).
    route_verified: Callable[[str, str, str], bool] | None = None
    # The id of one of the board's jobs named in a URL (Greenhouse: ?gh_jid=, or a path id), or None.
    job_id_in_url: Callable[[str], str | None] | None = None
    # URL patterns of the page the board shows for a closed or unknown posting (it redirects there).
    closed_url_patterns: list[str] = field(default_factory=list)

    # ── Discover ─────────────────────────────────────────────────────────────
    # JS that finds the apply call-to-action, tags it data-ja-field="apply-cta" and returns its
    # text (or null). None: discover.FIND_APPLY_JS.
    apply_button_js: str | None = None

    # ── Submit / verify ──────────────────────────────────────────────────────
    # JS that finds the submit button, tags it data-ja-field="submit-cta" and returns its text
    # (or null). None: controls.FIND_SUBMIT_JS, which uses the shared submit/advance classifier (a
    # "Next"/"Continue" step button is never the final submit; a hook must not return one either).
    submit_button_js: str | None = None
    # JS that tags the verification-code inputs data-ja-field="code-<i>" and returns how many
    # there are. None: submit.FIND_CODE_INPUTS_JS.
    code_inputs_js: str | None = None
    # Gmail subject filter for this board's verification-code emails (None: any email to the alias).
    email_code_subject: str | None = None
    # Regex the whole emailed code must match. Needed to accept codes without digits (gmail/parser.py).
    email_code_pattern: str | None = None

    # ── Models ───────────────────────────────────────────────────────────────
    model_notes: str = ""

    def matches_url(self, url: str) -> bool:
        return any(re.search(p, url) for p in self.url_patterns)

    def closed_at(self, url: str) -> bool:
        return any(re.search(p, url) for p in self.closed_url_patterns)


# ── Greenhouse: company career page -> Greenhouse's own hosted form ───────────────────────────────
# The company page only frames the form Greenhouse serves at embed/job_app?for=<board>&token=<id>;
# opening that directly works when the company page is behind bot protection or lazy-loads the
# iframe. The job id comes from ?gh_jid=, else (only for a dead-end page) from a job-id-shaped number
# in the URL path; the board token from the page if it names one, else guessed from the domain. Each
# pair is checked in the browser only (no API or other side channel): an unknown pair lands on the
# board's closed-job page (HTTP 200: a real board without that job; 404: no such board), a known one
# must pass greenhouse_route_verified. (job-boards.greenhouse.io/<board>/jobs/<id> is no alternative:
# for a company with its own career page it redirects back there.)
GH_JOB_ID = r"[?&]gh_jid=(\d+)(?=&|#|$)"  # a malformed id (123abc) names no job
GH_PATH_JOB_ID = r"[./](\d{6,})(?=[/?#]|$)"  # job.8274601, /jobs/8274601; matched on the path only
GREENHOUSE_FORM = "https://job-boards.greenhouse.io/embed/job_app?for={token}&token={job_id}"
GREENHOUSE_HOST = r"^https?://(job-boards|boards)(\.eu)?\.greenhouse\.io/"
GH_FORM_SIGNATURE = r"<form\b[^>]*\bid=\"application-form\"|\bid=\"grnhse_(app|iframe)\""
_GH_TOKEN_IN_PAGE = [
    r"greenhouse\.io/embed/job_(?:board|app)(?:/js)?\?(?:[^\"'\s<>]*?&(?:amp;)?)?for=([\w-]+)",
    r"(?:job-boards|boards)\.greenhouse\.io/(?!embed[/?])([\w-]+)/jobs/",
]
_HOST_NOISE = {"www", "careers", "career", "jobs", "job", "apply", "boards", "join", "work", "com", "org", "net"}
MAX_ROUTES = 4
PAGE_HTML_JS = "document.documentElement.outerHTML"


def _greenhouse_job_id(url: str, path_ids: bool = True) -> tuple[str, bool] | None:
    """(job id, named by gh_jid) for a URL, or None."""
    if m := re.search(GH_JOB_ID, url):
        return m.group(1), True
    if path_ids and (m := re.search(GH_PATH_JOB_ID, urlsplit(url).path)):
        return m.group(1), False
    return None


def greenhouse_job_id(url: str) -> str | None:
    found = _greenhouse_job_id(url)
    return found[0] if found else None


def greenhouse_token_candidates(page_url: str, html: str) -> list[str]:
    """Board tokens to try, most likely first: named in the page, then the domain's labels."""
    return list(dict.fromkeys(_tokens_in_page(html) + _tokens_from_host(page_url)))[:MAX_ROUTES]


def _tokens_in_page(html: str) -> list[str]:
    return list(dict.fromkeys(m.lower() for p in _GH_TOKEN_IN_PAGE for m in re.findall(p, html)))


def _tokens_from_host(page_url: str) -> list[str]:
    labels = (urlsplit(page_url).hostname or "").lower().split(".")[:-1]  # without the TLD
    found = []
    for label in reversed([lb for lb in labels if lb not in _HOST_NOISE and len(lb) > 2]):
        found += [label, label.replace("-", "")]
    return list(dict.fromkeys(found))


async def greenhouse_routes(page_url: str, run_js: RunJs, path_ids: bool) -> list[BoardRoute]:
    if re.match(GREENHOUSE_HOST, page_url) or not (found := _greenhouse_job_id(page_url, path_ids)):
        return []
    job_id, from_gh_jid = found
    try:
        html = str(await run_js(PAGE_HTML_JS) or "")
    except Exception:
        html = ""
    named = _tokens_in_page(html)
    return [BoardRoute(GREENHOUSE_FORM.format(token=quote(t), job_id=job_id), job_id, t, from_gh_jid and t in named)
            for t in greenhouse_token_candidates(page_url, html)]


def greenhouse_route_verified(landed_url: str, html: str, job_id: str) -> bool:
    """Greenhouse's host, the form's signature, and this job's id in the URL or the page: never
    another job's form."""
    id_in_url = re.search(rf"[?&]token={job_id}(?=&|#|$)|/jobs/{job_id}(?=[/?#]|$)", landed_url)
    id_in_page = re.search(rf"(?<!\d){job_id}(?!\d)", html)
    return bool(re.match(GREENHOUSE_HOST, landed_url) and re.search(GH_FORM_SIGNATURE, html)
                and (id_in_url or id_in_page))


GREENHOUSE = BoardProfile(
    name="greenhouse",
    url_patterns=[r"^https?://(job-boards(\.eu)?|boards)\.greenhouse\.io/", GH_JOB_ID, r"^https?://grnh\.se/"],
    dom_signature_js="!!document.querySelector('form#application-form, #grnhse_app, iframe#grnhse_iframe')",
    embed_patterns=[r"^https?://(job-boards(\.eu)?|boards)\.greenhouse\.io/embed/job_app"],
    application_routes=greenhouse_routes,
    route_verified=greenhouse_route_verified,
    job_id_in_url=greenhouse_job_id,
    # A closed or unknown job redirects to the board's job list with ?error=true (embed/job_board?for=
    # <board>&error=true, or /<board>?error=true). The flag anywhere else (a job page, the form, a
    # company site) is no evidence.
    closed_url_patterns=[r"^https?://(job-boards(\.eu)?|boards)\.greenhouse\.io/(embed/job_board|[\w-]+)/?"
                         r"\?([^#]*&)?error=true(&|#|$)"],
    # Greenhouse emails an 8-character code to the applicant after submit and shows 8 boxes.
    code_inputs_js=r"""
(() => {
  const boxes = [...document.querySelectorAll('input[id^="security-input-"]')]
    .sort((a, b) => parseInt(a.id.split('-').pop()) - parseInt(b.id.split('-').pop()));
  boxes.forEach((el, i) => { el.dataset.jaField = `code-${i}`; });
  return boxes.length;
})()
""",
    email_code_subject="security code",
    email_code_pattern=r"[A-Za-z0-9]{8}",
    model_notes="""This is a Greenhouse job board. Known behaviour (verified on job-boards.greenhouse.io):
- The application form is on the same page as the job description, below it. The "Apply" button only scrolls to it.
- Dropdowns are React-select comboboxes. Their options are NOT in the page until the dropdown is open. The text
  input inside is tiny, so clicking it often misses; focus it and press ArrowDown, or click its "Toggle flyout"
  button. Close with Escape. Don't press Enter or click an option unless you mean to select it.
- The Phone field has its own country-code picker (a separate widget with ~244 hidden country options). It is not
  the "Country" dropdown; don't mix them up.
- Resume/CV and Cover Letter are file inputs hidden behind "Attach" buttons (alternatives: Dropbox, Google Drive,
  "Enter manually"). Whether they're required is marked on the surrounding group with an asterisk.
- Custom questions have ids like question_<number>. Required fields are marked with "*".
- There's no structured job data (JSON-LD). The job title is the main heading; the company is in the page title.
- Company career sites often embed Greenhouse in an iframe (boards.greenhouse.io/embed/job_app?...) or link to it
  with a ?gh_jid=<id> parameter.
- After submitting, Greenhouse may ask for an 8-character security code that it emails to the applicant, entered
  into 8 separate boxes (#security-input-0 ... #security-input-7), then the form is submitted again.""",
)

GENERIC = BoardProfile(name="generic")

# Order matters only when two boards' URL patterns could both match.
PROFILES: list[BoardProfile] = [GREENHOUSE]


async def board_routes(board: BoardProfile, page_url: str, run_js: RunJs, dead_end: bool
                       ) -> list[tuple[BoardProfile, BoardRoute]]:
    """The candidate board forms for a page: from its own board; on a dead-end page, whose board
    can't be told, from every board that can read a job id off the URL."""
    boards = [board] if board.application_routes else []
    if dead_end:
        boards += [p for p in PROFILES if p.application_routes and p is not board]
    routes = [(b, r) for b in boards for r in await b.application_routes(page_url, run_js, dead_end)]
    return routes[:MAX_ROUTES]


def board_id_in_url(url: str) -> bool:
    return any(p.job_id_in_url(url) for p in PROFILES if p.job_id_in_url)


def record_blocked(url: str, board: BoardProfile, signal: str) -> None:
    """One _observations.jsonl row per blocked discover: how often a walled page named no board job
    (the case a headed resolve would be for; contract §2)."""
    path = Path(settings.artifacts_dir) / "_observations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({
            "t": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "blocked", "url": url, "board": board.name,
            "signal": signal, "board_id_in_url": board_id_in_url(url),
        }, ensure_ascii=False) + "\n")


def by_name(name: str) -> BoardProfile:
    return next((p for p in PROFILES if p.name == name), GENERIC)


async def detect_board(run_js: RunJs, url: str) -> BoardProfile:
    """URL first (cheap, reliable), then DOM signatures for boards on company domains."""
    for p in PROFILES:
        if p.matches_url(url):
            return p
    for p in PROFILES:
        if p.dom_signature_js:
            try:
                if await run_js(p.dom_signature_js):
                    return p
            except Exception:
                pass
    return GENERIC


async def embedded_form_url(run_js: RunJs) -> tuple[BoardProfile, str] | None:
    """If the page embeds a known board's form in an iframe, the iframe's URL (open that instead)."""
    srcs = json.loads(await run_js("JSON.stringify([...document.querySelectorAll('iframe[src]')].map(f => f.src))"))
    for p in PROFILES:
        for src in srcs:
            if any(re.search(pat, src) for pat in p.embed_patterns):
                return p, src
    return None


def with_notes(text: str, board: BoardProfile) -> str:
    """Append the board's notes to a model prompt or agent task."""
    if not board.model_notes:
        return text
    return f"{text}\n\nNotes about this job board (from previous runs):\n{board.model_notes}"


def record_observation(run_id: str, url: str, board: BoardProfile, stages: list[dict], n_fields: int) -> None:
    path = Path(settings.artifacts_dir) / "_observations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({
            "t": time.strftime("%Y-%m-%d %H:%M:%S"), "run_id": run_id, "url": url,
            "board": board.name, "fields": n_fields, "stages": stages,
        }, ensure_ascii=False) + "\n")
