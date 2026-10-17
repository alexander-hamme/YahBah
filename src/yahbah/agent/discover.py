"""
Reach the application form and map its fields: deterministic code first, models as fallbacks.

Discover (get to the application form):
  1. read_form_schema finds a form already; else click a visible "Apply" button  -- code
  2. one-shot: the page's indexed elements -> one element to click (≤2 rounds)  -- driver
  3. short navigation agent                                                     -- driver (+ escalation)
  Every tier's result is checked in code (read_form_schema) before it's accepted; anything short of
  a visible form — even a model's "job closed" — falls through to the next tier.
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
import time
from dataclasses import dataclass
from typing import Literal

from browser_use.browser import BrowserSession
from browser_use.browser.events import ClickElementEvent, SwitchTabEvent
from browser_use.llm.messages import SystemMessage, UserMessage
from loguru import logger
from pydantic import BaseModel
from temporalio import activity

from yahbah.agent.boards import BoardProfile, detect_board, embedded_form_url, with_notes
from yahbah.agent.fill import capture_empty_values
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
    """A stage could not complete. reason is a short code (job_closed, login_wall, captcha,
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


_NAV_FAILURES = {"job_closed": "job_closed", "login_required": "login_wall", "captcha": "captcha"}


@dataclass
class DiscoverResult:
    board: BoardProfile
    page_url: str
    job: dict


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
    return False


async def discover(session: BrowserSession, trace: Trace) -> DiscoverResult:
    """From the job URL's page to a page showing the application form. Raises StageFailed."""
    # Job info first: the page we were given usually holds the description; the form may not.
    job = await read_job_info(session)
    await _follow_embed(session, trace)
    board = await _detect(session)

    if board.resolve_application_url:
        t = time.time()
        if real := await board.resolve_application_url(lambda c: run_js(session, c)):
            await session.navigate_to(real)
            await settle(3)
            trace.add("discover", "code", t, f"{board.name}: resolved application URL {real[:80]}")
            await _follow_embed(session, trace)
            board = await _detect(session)

    t = time.time()
    if looks_like_form(fields := await get_form_schema(session)):
        trace.add("discover", "code", t, f"form already on page ({len(fields)} fields)")
        return DiscoverResult(board, await session.get_current_page_url(), job)

    t = time.time()
    clicked = await click_apply(session, board)
    await _follow_embed(session, trace)
    if looks_like_form(fields := await get_form_schema(session)):
        trace.add("discover", "code", t, f"clicked {clicked!r}, form found ({len(fields)} fields)")
        return DiscoverResult(await _detect(session), await session.get_current_page_url(), job)
    trace.add("discover", "code", t, f"clicked {clicked!r}, no form" if clicked else "no apply button found")

    if await oneshot_navigate(session, board, trace):
        return DiscoverResult(await _detect(session), await session.get_current_page_url(), job)

    t = time.time()
    history = await run_agent(
        with_notes(load_prompts_config()["prompts"]["agent_discover_task"], board),
        make_crew(), session, max_steps=6, output=NavOutcome,
    )
    outcome: NavOutcome | None = history.structured_output
    await _follow_embed(session, trace)
    fields = await get_form_schema(session)
    trace.add("discover", "model", t, f"navigation agent -> {outcome.state if outcome else 'no result'}, "
                                      f"{len(fields)} fields")
    if looks_like_form(fields):
        # Navigation may have landed on a different board (e.g. company site -> Greenhouse).
        return DiscoverResult(await _detect(session), await session.get_current_page_url(), job)
    if outcome and outcome.state in _NAV_FAILURES:
        raise StageFailed(_NAV_FAILURES[outcome.state], outcome.explanation)
    raise StageFailed("form_not_found", outcome.explanation if outcome else "navigation agent gave no result")


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
         "autocomplete": f.get("autocomplete")}
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
