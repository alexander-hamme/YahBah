"""
Gated submit and post-submit verification. Submitting is done only here, by code: no agent ever
gets a submit tool (and the click/Enter actions agents do have are guarded, see controls.py).

Code evidence decides; the model advises. submit_and_verify():
  gate      in code, nothing recorded yet: no unanswered required field, no empty required field,
            no validation error (StageFailed needs_answer / fill_failed)
  locate    the submit control (controls.FIND_SUBMIT_JS, or the board's hook). None -> StageFailed
            submit_button_not_found; a step button ("Next", "Continue") is never the final submit
  claim     claim() -> bool, once, right before the first click. False -> StageFailed claim_denied, no click
  click     the located control
  verify    rounds, at most settings.submit_max_attempts. Each round waits (condition-based, ROUND_SECONDS)
            for the page to show something decisive, then reads structural evidence:
              submitted    our form and its submit control are gone, nothing shows an error, and the page says
                           the application was received (confirmation markers)
              probable     same, without a confirmation marker: only a model verdict "submitted" completes it
              errors       the form is still there with validation errors found in code: fix, then click again
              code_prompt  verification-code inputs: fetch the code from Gmail, enter it, click again
              pending      nothing decisive (maybe in flight): wait another round, never click
            A click that changed nothing, or "thank you" text with the form still on the page, is pending.
            The model (PostSubmitState) is asked only when code can't settle it (probable, or the last round
            still pending); it can complete "probable" and name a blocker (captcha, login wall), never more.
  final     the observation after the last re-click; then StageFailed submit_unverified if still unproven.

Submit is only ever clicked again on code evidence: validation errors found in the DOM, or code inputs.
`already_attempted` (an activity retry after a click) means verify only, with the same rule.
Everything after the claim that doesn't end in verified success is StageFailed (never a flagged result);
the caller turns that into SUBMIT_UNCERTAIN.
"""
import asyncio
import json
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from browser_use.browser import BrowserSession
from browser_use.browser.events import ClickElementEvent
from loguru import logger
from pydantic import BaseModel

from yahbah.agent.boards import BoardProfile, with_notes
from yahbah.agent.controls import CLASSIFY_THIS_JS, FIND_SUBMIT_JS, PAGE_EVIDENCE_JS, snapshot
from yahbah.agent.discover import PAGE_TEXT_JS, StageFailed
from yahbah.agent.fill import (
    Beat,
    FillContext,
    FillError,
    MapNewFields,
    fill_new_fields,
    fix_with_agent,
    problems_for,
    read_errors,
    read_values,
    write_field,
)
from yahbah.agent.tools import close_open_dropdowns, focus_key, node_for_key, run_js
from yahbah.config import load_prompts_config, settings
from yahbah.gmail.client import GmailClient
from yahbah.llm.client import LLMError, OllamaClient

# Timing. A round ends as soon as the page shows something decisive on two polls in a row; these only bound
# how long it waits for that.
ROUND_SECONDS = 4.0            # one observation window
POLL_SECONDS = 0.4
IN_FLIGHT_EXTRA_SECONDS = 20.0  # a spinner, aria-busy or a disabled submit control extends the window this far
ENABLE_WAIT_SECONDS = 3.0      # a disabled submit control gets this long to become enabled

# Verification-code inputs, tagged data-ja-field="code-<i>"; returns how many.
# One box per character (maxlength=1, at least 4 of them), else a one-time-code input.
FIND_CODE_INPUTS_JS = r"""
(() => {
  const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  let boxes = [...document.querySelectorAll('input')].filter((e) => vis(e) && e.maxLength === 1);
  if (boxes.length < 4) boxes = [...document.querySelectorAll('input[autocomplete="one-time-code"]')].filter(vis);
  boxes.forEach((el, i) => { el.dataset.jaField = `code-${i}`; });
  return boxes.length;
})()
"""

CONFIRMATION_HTML_JS = r"""
(() => (document.querySelector('main, [role=main]') || document.body).innerHTML.slice(0, 4000))()
"""

# What a page says when an application was received / when it says something went wrong. Matched against the
# visible text of the main content: a shortcut for the common phrasings, not a model. A miss only means the
# evidence is "probable" instead of "submitted".
CONFIRMATION = re.compile(
    r"\b(thank(s| you)( so much)? for (applying|your (application|submission)|submitting)"
    r"|(your |the )?application (has been |was |is )?(successfully )?(received|submitted|sent|complete|completed)"
    r"|we('ve| have)? (successfully )?received your (application|submission)"
    r"|(successfully|already) (submitted|applied)"
    r"|you('ve| have) (successfully )?(applied|submitted)"
    r"|application (confirmation|confirmed))\b", re.I)
NEGATIVE = re.compile(
    r"\b(something went wrong|an error (has )?occurred|error occurred|unable to (submit|process|complete)"
    r"|failed to submit|could ?n[o']t (be )?submit(ted)?|not (yet )?(been )?submitted|please try again|try again later"
    r"|(page|job) not found|access denied|server error|bad gateway|service unavailable)\b", re.I)

Verdict = Literal["submitted", "probable", "errors", "code_prompt", "pending"]
Claim = Callable[[], Awaitable[bool]]
Record = Callable[[dict], Awaitable[None]]


def find_markers(pattern: re.Pattern, text: str) -> list[str]:
    seen: list[str] = []
    for m in pattern.finditer(text):
        phrase = re.sub(r"\s+", " ", m.group(0).lower())
        if phrase not in seen:
            seen.append(phrase)
    return seen[:5]


# ── Evidence ──────────────────────────────────────────────────────────────────

@dataclass
class Baseline:
    """The page we are about to submit (or, verify-only, the form we filled)."""

    url: str
    token: str | None   # stamped on the document: absent from a document that replaced it
    controls: int       # visible fillable controls


@dataclass
class SubmissionEvidence:
    """What the page shows now, measured in code (JSON-serializable through as_dict)."""

    url: str
    doc_replaced: bool
    controls: int          # visible fillable controls now
    controls_before: int
    cta_present: bool      # the submit control we clicked (still in the DOM / visible / disabled)
    cta_visible: bool
    cta_disabled: bool
    errors: list[dict]     # validation errors found in the DOM: [{key, message}]
    code_inputs: int
    in_flight: bool        # spinner, aria-busy, or the submit control disabled
    confirmation: list[str]
    negative: list[str]
    excerpt: str           # untrusted page text, for the human reading the record

    @property
    def form_gone(self) -> bool:
        """Fewer than half of the fillable controls we started with are left."""
        return self.controls_before > 0 and self.controls < max(1, math.ceil(self.controls_before / 2))

    def as_dict(self) -> dict:
        return {
            "doc_replaced": self.doc_replaced, "form_gone": self.form_gone,
            "controls": self.controls, "controls_before": self.controls_before,
            "submit_control": {"present": self.cta_present, "visible": self.cta_visible, "disabled": self.cta_disabled},
            "errors": self.errors, "code_inputs": self.code_inputs, "in_flight": self.in_flight,
            "confirmation_markers": self.confirmation, "error_markers": self.negative, "excerpt": self.excerpt,
        }


def assess(ev: SubmissionEvidence) -> Verdict:
    """The structural verdict. Pure: this is the decision, the model only advises."""
    if ev.code_inputs:
        return "code_prompt"
    if not ev.form_gone:
        return "errors" if ev.errors else "pending"  # still our form: failed (errors), in flight, or a no-op click
    if ev.cta_visible:
        return "pending"  # the form's fields are gone but its submit control is not
    if ev.confirmation and not ev.negative:
        return "submitted"
    if ev.in_flight or ev.negative:
        return "pending"
    return "probable"  # form and control gone, nothing says why


async def read_baseline(session: BrowserSession, ctx: FillContext, already_attempted: bool) -> Baseline:
    snap = await snapshot(session)
    # verify-only: the page may be anything by now; the form we filled is what to compare against
    return Baseline(snap.url, snap.token, len(ctx.fields) if already_attempted else snap.controls)


async def read_evidence(session: BrowserSession, board: BoardProfile, baseline: Baseline) -> SubmissionEvidence:
    page = json.loads(await run_js(session, PAGE_EVIDENCE_JS))
    text = f"{page['title']} {page['text']}"
    cta = page["cta"]
    return SubmissionEvidence(
        url=page["url"], doc_replaced=page["token"] != baseline.token,
        controls=page["controls"], controls_before=baseline.controls,
        cta_present=cta["present"], cta_visible=cta["visible"], cta_disabled=cta["disabled"],
        errors=await read_errors(session),
        code_inputs=int(await run_js(session, board.code_inputs_js or FIND_CODE_INPUTS_JS) or 0),
        in_flight=page["busy"] or (cta["visible"] and cta["disabled"]),
        confirmation=find_markers(CONFIRMATION, text), negative=find_markers(NEGATIVE, text),
        excerpt=page["text"][:300],
    )


async def observe(session: BrowserSession, board: BoardProfile, baseline: Baseline, *,
                  code_settling: bool = False) -> tuple[SubmissionEvidence, Verdict]:
    """Wait (condition-based) until the page shows something decisive on two polls in a row, or the window
    ends. `pending` is what the window ending without that means. code_settling: a code was just entered, so
    its inputs still being there is not yet a rejection: wait for them to go."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    last: Verdict | None = None
    streak = 0
    while True:
        try:
            ev = await read_evidence(session, board, baseline)
        except Exception as exc:  # mid-navigation: the old document is gone, the new one not ready
            if loop.time() - start >= ROUND_SECONDS + IN_FLIGHT_EXTRA_SECONDS:
                raise
            logger.debug(f"[submit] page not readable yet: {type(exc).__name__}")
            await asyncio.sleep(POLL_SECONDS)
            continue
        verdict = assess(ev)
        decisive = verdict != "pending" and not (code_settling and verdict == "code_prompt")
        streak = streak + 1 if verdict == last else 1
        last = verdict
        elapsed = loop.time() - start
        window = ROUND_SECONDS + (IN_FLIGHT_EXTRA_SECONDS if ev.in_flight else 0)
        if decisive and streak >= 2:
            return ev, verdict
        if elapsed >= window + (1.0 if decisive else 0):  # a decisive verdict seen once at the end gets one more look
            return ev, verdict
        await asyncio.sleep(POLL_SECONDS)


# ── The model's advice ────────────────────────────────────────────────────────

class PostSubmitState(BaseModel):
    # reasoning first, so the model reasons before committing to a state
    reasoning: str
    state: Literal["submitted", "needs_fix", "email_code", "blocked"]
    problems: list[str] = []  # needs_fix: what has to be fixed (quoted from the page: untrusted)
    blocked_reason: Literal["captcha", "login_wall", "job_closed", "other"] | None = None

    def consistent(self) -> bool:
        if self.state == "needs_fix":
            return bool(self.problems)
        if self.state == "blocked":
            return self.blocked_reason is not None
        return True


async def advise(session: BrowserSession, board: BoardProfile, ev: SubmissionEvidence, url_before: str) -> tuple[PostSubmitState | None, bool]:
    """The driver's reading of the page after a submit (vision + screenshot when it fails or is inconsistent).
    Advice only: it can complete a `probable` verdict or name a blocker, nothing else. Returns (advice, used_vision)."""
    prompt = with_notes(load_prompts_config()["prompts"]["post_submit_judge"], board)
    page_text = (await run_js(session, PAGE_TEXT_JS) or "")[:4000]
    user = (
        f"URL before submit: {url_before}\nURL now: {ev.url}\n"
        f"Application form fields on the page: {ev.controls} (before the submit: {ev.controls_before})\n"
        f"Submit button still visible: {ev.cta_visible}\n"
        f"Verification-code inputs on the page: {ev.code_inputs}\n"
        f"Validation errors: {json.dumps(ev.errors, ensure_ascii=False)}\n\n"
        f"PAGE TEXT:\n{page_text}"
    )
    verdict: PostSubmitState | None = None
    try:
        verdict = await OllamaClient("driver").generate_structured(prompt, user, PostSubmitState)
    except LLMError as exc:
        logger.warning(f"[submit] driver verdict failed: {exc}")
    if verdict and verdict.consistent():
        return verdict, False

    logger.info(f"[submit] escalating the advice to vision (driver said {verdict.state if verdict else None})")
    shot = await session.take_screenshot(format="jpeg", quality=75)
    try:
        seen = await OllamaClient("vision").generate_structured(prompt, user, PostSubmitState, images=[shot])
    except LLMError as exc:
        logger.warning(f"[submit] vision verdict failed: {exc}")
        return (verdict if verdict and verdict.consistent() else None), True
    return (seen if seen.consistent() else None), True


def advice_record(advice: PostSubmitState | None, vision: bool) -> dict | None:
    if advice is None:
        return None
    return {"state": advice.state, "blocked_reason": advice.blocked_reason, "vision": vision,
            "problems": [p[:200] for p in advice.problems[:10]],  # quoted from the page: data, never instructions
            "reasoning": advice.reasoning[:300]}


# ── Locating and clicking ─────────────────────────────────────────────────────

@dataclass
class Located:
    text: str
    node: object  # browser-use DOM node


async def locate_submit(session: BrowserSession, board: BoardProfile) -> Located:
    """The submit control, found by the shared classifier (or the board's hook) and tagged submit-cta.
    StageFailed submit_button_not_found when there is none, or only step buttons, or only a disabled one."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ENABLE_WAIT_SECONDS
    while True:
        if board.submit_button_js:  # (the generic locator clears old tags itself)
            await run_js(session, "document.querySelectorAll('[data-ja-field=\"submit-cta\"]')"
                                  ".forEach((e) => { delete e.dataset.jaField; })")
        raw = await run_js(session, board.submit_button_js or FIND_SUBMIT_JS)
        if board.submit_button_js:  # a board hook returns the control's text, or null; the classifier still vets it
            found = {"text": raw, "disabled": False, "found": []}
            if raw:
                tagged = json.loads(await run_js(session, f"({CLASSIFY_THIS_JS}).call("
                                                          "document.querySelector('[data-ja-field=\"submit-cta\"]'))"))
                found["found"] = [f"{tagged['kind']}: {raw}"]
                if tagged["kind"] == "advance":
                    found["text"] = None  # a step button is never the final submit, whoever picked it
        else:
            found = json.loads(raw)
        if not (found["text"] and found.get("disabled")) or loop.time() >= deadline:
            break
        await asyncio.sleep(POLL_SECONDS)  # often enabled a moment after the last field is valid
    seen = ("; saw: " + ", ".join(found["found"][:6]))[:300] if found.get("found") else ""
    if not found["text"]:
        raise StageFailed("submit_button_not_found", f"no submit control found on the form{seen}")
    if found.get("disabled"):
        raise StageFailed("submit_button_not_found", f"the submit control {found['text'][:60]!r} stays disabled")
    node = await node_for_key(session, "submit-cta")
    if node is None:
        raise StageFailed("submit_button_not_found", f"the submit control {found['text'][:60]!r} is not clickable")
    return Located(found["text"], node)


async def click_located(session: BrowserSession, located: Located) -> None:
    ev = session.event_bus.dispatch(ClickElementEvent(node=located.node))
    await ev
    await ev.event_result(raise_if_any=True, raise_if_none=False)
    logger.info(f"[submit] clicked {located.text!r}")


# ── Verification codes ────────────────────────────────────────────────────────

async def enter_code(session: BrowserSession, code: str, n_inputs: int) -> None:
    """Type the code into the page's code inputs and read it back."""
    if n_inputs > 1:  # one box per character
        keys, chunks = [f"code-{i}" for i in range(n_inputs)], list(code[:n_inputs])
    elif n_inputs == 1:
        keys, chunks = ["code-0"], [code]
    else:
        raise StageFailed("email_code_entry", "the page asks for a code but no code input was found")
    cdp = await session.get_or_create_cdp_session()
    for key, chunk in zip(keys, chunks):
        if not await focus_key(session, key):
            raise StageFailed("email_code_entry", f"code input {key} is not focusable")
        await cdp.cdp_client.send.Input.insertText(params={"text": chunk}, session_id=cdp.session_id)
    shown = json.loads(await run_js(session, "JSON.stringify(%s.map((k) => document.querySelector("
                                             "`[data-ja-field=\"${k}\"]`)?.value ?? ''))" % json.dumps(keys)))
    if "".join(shown) != "".join(chunks):
        raise StageFailed("email_code_entry", "the code inputs do not show the code that was typed")


# ── Fixing what the page complained about ─────────────────────────────────────

async def current_problems(session: BrowserSession, ctx: FillContext) -> list[str]:
    await close_open_dropdowns(session)
    return problems_for(ctx, await read_values(session, list(ctx.fields)), await read_errors(session))


async def fix_problems(session: BrowserSession, board: BoardProfile, ctx: FillContext, errors: list[dict],
                       map_new_fields: MapNewFields | None, beat: Beat) -> None:
    """New fields (mapped from the profile, or recorded in ctx.unmapped_new_fields when nothing may be mapped),
    then the fields named in validation errors (rewritten in code), then the fix agent for what is still wrong."""
    await fill_new_fields(session, ctx, map_new_fields, beat)
    for key in {e["key"] for e in errors if e.get("key") in ctx.fields}:
        if value := ctx.value_for(key):
            try:
                await write_field(session, ctx, key, value)
            except FillError as exc:
                logger.warning(f"[submit] re-fill [{key}] failed: {exc}")
    if remaining := await current_problems(session, ctx):
        await fix_with_agent(session, board, ctx, remaining)


def _unanswered(ctx: FillContext) -> str:
    return ", ".join(repr(f["label"][:60]) for f in ctx.unmapped_new_fields[:5])


async def gate(session: BrowserSession, ctx: FillContext, map_new_fields: MapNewFields | None, beat: Beat) -> None:
    """Before anything is recorded or clicked. New fields are mapped and filled when a mapper is given; otherwise
    a required one without an answer stops us (it was not part of what was approved)."""
    await fill_new_fields(session, ctx, map_new_fields, beat)
    if ctx.unmapped_new_fields:
        raise StageFailed("needs_answer", f"required field(s) with no answer on the form: {_unanswered(ctx)}")
    if problems := await current_problems(session, ctx):
        raise StageFailed("fill_failed", "; ".join(problems)[:1000])


# ── The loop ──────────────────────────────────────────────────────────────────

@dataclass
class SubmitResult:
    confirmation_url: str
    confirmation_text: str
    confirmation_html: str
    evidence: dict = field(default_factory=dict)  # the closing evidence record, as passed to `record`


class _Recorder:
    """Builds one JSON-serializable record per observation and hands it to `record` (best effort: a lost
    record must never turn a submitted application into an exception)."""

    def __init__(self, record: Record | None, baseline: Baseline, attempt: str) -> None:
        self.record, self.baseline, self.attempt = record, baseline, attempt
        self.started = time.monotonic()

    async def __call__(self, *, kind: str, rnd: int, clicks: int, ev: SubmissionEvidence, verdict: str, action: str,
                       advice: dict | None = None, decided_by: str = "code", code: dict | None = None) -> dict:
        rec = {
            "schema": 1, "kind": kind, "round": rnd, "at": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.monotonic() - self.started, 1), "clicks": clicks, "attempt": self.attempt,
            "url_before": self.baseline.url, "url": ev.url, "verdict": verdict, "decided_by": decided_by,
            "action": action, "signals": ev.as_dict(), "judge": advice, "code": code,
        }
        if self.record:
            try:
                await self.record(rec)
            except Exception as exc:
                logger.warning(f"[submit] could not record the evidence: {exc}")
        return rec


async def _result(session: BrowserSession, ev: SubmissionEvidence, rec: dict) -> SubmitResult:
    return SubmitResult(
        confirmation_url=ev.url,
        confirmation_text=(await run_js(session, PAGE_TEXT_JS) or "")[:2000],
        confirmation_html=await run_js(session, CONFIRMATION_HTML_JS) or "",
        evidence=rec,
    )


def _why_unverified(ev: SubmissionEvidence, verdict: Verdict) -> str:
    if verdict == "code_prompt":
        return "the page still asks for a verification code"
    if verdict == "errors":
        return "the form is still there with validation errors"
    if not ev.form_gone:
        return "the form and its submit control are still on the page and nothing says it was received"
    return "the form is gone but nothing confirms the application was received"


async def _verify(session: BrowserSession, board: BoardProfile, ctx: FillContext, email: str, baseline: Baseline,
                  rec: _Recorder, clicks: int, epoch: int, map_new_fields: MapNewFields | None,
                  beat: Beat) -> SubmitResult:
    max_rounds = max(1, settings.submit_max_attempts)
    gmail: GmailClient | None = None
    tried: set[str] = set()      # verification-code emails used so far
    entered: str | None = None   # the email whose code was just typed, not yet known to be accepted
    advised = False              # the model has been asked about a `probable` page
    seen_errors: set[tuple] = set()  # error sets already answered with a re-click
    closing = False              # the observation after the last re-click
    rnd = 0
    while True:
        rnd += 1
        beat(f"submit: verifying (round {rnd})")
        ev, verdict = await observe(session, board, baseline, code_settling=entered is not None)
        code_note = None
        if entered is not None:
            accepted = not ev.code_inputs  # the page moved on from the code inputs
            code_note = {"message_id": entered, "outcome": "accepted" if accepted else "rejected"}
            if accepted and gmail is not None:
                try:
                    await gmail.finalize_code_message(entered)
                except Exception as exc:  # mailbox housekeeping must not undo a submit
                    logger.warning(f"[submit] could not finalize the code email {entered}: {exc}")
            entered = None
        kind = "final" if closing else "round"

        if verdict == "submitted":
            beat("submit: confirmed")
            return await _result(session, ev, await rec(kind=kind, rnd=rnd, clicks=clicks, ev=ev, verdict=verdict,
                                                        action="accepted", code=code_note))

        terminal = closing or (rnd >= max_rounds and verdict in ("pending", "probable"))
        advice, vision = None, False
        if (verdict == "probable" and not advised) or (verdict == "pending" and terminal):
            beat("submit: asking the model for a second opinion")
            (advice, vision), advised = await advise(session, board, ev, baseline.url), True
        if verdict == "probable" and advice and advice.state == "submitted":
            return await _result(session, ev, await rec(
                kind=kind, rnd=rnd, clicks=clicks, ev=ev, verdict="submitted", action="accepted",
                advice=advice_record(advice, vision), decided_by="code+model", code=code_note))

        can_click = not closing and verdict in ("errors", "code_prompt")
        action = ("fix_and_reclick" if verdict == "errors" else "enter_code_and_reclick") if can_click else (
            "stop" if terminal or verdict in ("errors", "code_prompt") else "waiting")
        await rec(kind=kind, rnd=rnd, clicks=clicks, ev=ev, verdict=verdict, action=action,
                  advice=advice_record(advice, vision), code=code_note)

        if action == "waiting":
            continue
        if action == "stop":
            if advice and advice.state == "blocked" and advice.blocked_reason:
                reason = "blocked" if advice.blocked_reason == "other" else advice.blocked_reason
                raise StageFailed(reason, advice.reasoning[:500])
            raise StageFailed("submit_unverified", f"after {clicks} click(s): {_why_unverified(ev, verdict)}")

        if verdict == "errors":
            signature = tuple(sorted((e.get("key") or "", e.get("message") or "") for e in ev.errors))
            if signature in seen_errors:  # a second click on the same complaint would only risk a duplicate
                raise StageFailed("fill_failed", "the page shows the same validation errors after they were fixed: "
                                                 + "; ".join(f"{k} {m}".strip() for k, m in signature)[:400])
            seen_errors.add(signature)
            if ev.doc_replaced:
                raise StageFailed("submit_unverified", "the page reloaded with validation errors; its fields can "
                                                       "no longer be addressed")
            beat("submit: fixing what the page complained about")
            await fix_problems(session, board, ctx, ev.errors, map_new_fields, beat)
            if ctx.unmapped_new_fields:
                raise StageFailed("fill_failed", "required field(s) appeared after the submit and have no answer: "
                                                 f"{_unanswered(ctx)}")
            if still := await current_problems(session, ctx):
                raise StageFailed("fill_failed", "; ".join(still)[:1000])
            try:
                located = await locate_submit(session, board)
            except StageFailed as exc:  # after the claim this is no longer "no button": the outcome is unknown
                raise StageFailed("submit_unverified", f"cannot click submit again: {exc.detail}") from exc
        else:  # code_prompt
            beat("submit: waiting for the email code")
            gmail = gmail or GmailClient()
            try:
                found = await gmail.wait_for_verification_code(
                    email, since_epoch=epoch, subject_hint=board.email_code_subject,
                    code_pattern=board.email_code_pattern, exclude_message_ids=tried)
            except RuntimeError as exc:
                raise StageFailed("email_code_timeout", str(exc)) from exc
            tried.add(found.message_id)
            await enter_code(session, found.code, ev.code_inputs)
            entered = found.message_id
            try:
                located = await locate_submit(session, board)
            except StageFailed:
                located = None  # the code page may submit on its own, or through a control we don't know
        if located:
            clicks += 1
            await click_located(session, located)
        closing = rnd >= max_rounds


async def submit_and_verify(
    session: BrowserSession,
    board: BoardProfile,
    ctx: FillContext,
    email: str,
    already_attempted: bool,
    claim: Claim,
    map_new_fields: MapNewFields | None = None,
    beat: Beat = lambda _: None,
    record: Record | None = None,
) -> SubmitResult:
    """Submit the filled form and prove it went through, or raise StageFailed.

    claim: called once, after the submit control is located and right before the first click; False means
      another run holds the claim (StageFailed claim_denied, nothing clicked).
    record: called with a JSON-serializable evidence dict per observation (documented in wiki modules/agent.md).
    Before the claim failures are ordinary (needs_answer, fill_failed, submit_button_not_found, claim_denied).
    After it, anything but verified success is StageFailed, mostly submit_unverified: the caller ends the run
    SUBMIT_UNCERTAIN and never clicks submit for it again.
    """
    clicks = 0
    epoch = int(time.time())
    ctx.locked = map_new_fields is None  # no mapper: the mappings are the approved answers, the form must match them
    if ctx.locked and ctx.baseline is None:
        # The approved-state baseline (what fields without an approved answer showed once code had filled the form,
        # before any agent ran) comes from fill_form and is carried here as data. Taken now it would read whatever an
        # agent typed in between as a starting value, so without it nothing is checked: fail closed.
        raise StageFailed("fill_failed", "no approved-state baseline: the fill step's FillContext.baseline must be "
                                         "carried to the submit step")

    if already_attempted:
        logger.warning("[submit] a previous attempt already clicked submit: verifying only")
        baseline = await read_baseline(session, ctx, already_attempted)
    else:
        await gate(session, ctx, map_new_fields, beat)
        located = await locate_submit(session, board)
        baseline = await read_baseline(session, ctx, already_attempted)  # the form as it is when the click goes out
        beat("submit: claiming the submission")
        if not await claim():
            raise StageFailed("claim_denied", "the submission claim was not granted; nothing was clicked")
        epoch, clicks = int(time.time()), 1
        try:
            beat("submit: clicking")
            await click_located(session, located)
        except Exception as exc:
            raise StageFailed("submit_unverified", f"clicking submit failed after the claim "
                                                   f"({type(exc).__name__}: {exc})"[:500]) from exc
    rec = _Recorder(record, baseline, "verify_only" if already_attempted else "first")

    try:
        return await _verify(session, board, ctx, email, baseline, rec, clicks, epoch, map_new_fields, beat)
    except StageFailed:
        raise
    except Exception as exc:  # after the claim an unexpected error means "outcome unknown", not "retry"
        raise StageFailed("submit_unverified", f"{type(exc).__name__}: {exc}"[:500]) from exc
