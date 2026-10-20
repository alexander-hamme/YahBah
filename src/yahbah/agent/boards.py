"""
Per-job-board knowledge, kept separate so learning one board can't silently change another.

A BoardProfile holds:
- detection: URL patterns, plus a DOM check for boards served on company domains
- embeds: iframe URLs that hold the real form (open those directly; our readers see the top frame)
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

from yahbah.config import settings

RunJs = Callable[[str], Awaitable[object]]


@dataclass
class BoardProfile:
    name: str

    # ── Detection ────────────────────────────────────────────────────────────
    url_patterns: list[str] = field(default_factory=list)  # regexes matched against the page URL
    dom_signature_js: str | None = None  # JS expression returning true on this board's pages

    # ── Open ─────────────────────────────────────────────────────────────────
    # Iframe src patterns: if a company page embeds this board's form, open the iframe URL directly.
    embed_patterns: list[str] = field(default_factory=list)
    # Aggregators (job sites that link out to the real application): returns the real application
    # URL for the current page, or None. Not used by any board yet.
    resolve_application_url: Callable[[RunJs], Awaitable[str | None]] | None = None

    # ── Discover ─────────────────────────────────────────────────────────────
    # JS that finds the apply call-to-action, tags it data-ja-field="apply-cta" and returns its
    # text (or null). None: discover.FIND_APPLY_JS.
    apply_button_js: str | None = None

    # ── Submit / verify ──────────────────────────────────────────────────────
    # JS that finds the submit button, tags it data-ja-field="submit-cta" and returns its text
    # (or null). None: submit.FIND_SUBMIT_JS.
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


GREENHOUSE = BoardProfile(
    name="greenhouse",
    url_patterns=[r"^https?://(job-boards(\.eu)?|boards)\.greenhouse\.io/", r"[?&]gh_jid=\d+", r"^https?://grnh\.se/"],
    dom_signature_js="!!document.querySelector('form#application-form, #grnhse_app, iframe#grnhse_iframe')",
    embed_patterns=[r"^https?://(job-boards(\.eu)?|boards)\.greenhouse\.io/embed/job_app"],
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
