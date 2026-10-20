"""
Gated submit and post-submit verification. Submitting is done only here, by code: no agent ever
gets a submit tool.

submit_and_verify():
  gate      in code: no empty required fields, no validation errors (else StageFailed: fill_failed)
  record    submit_attempted_at is written to the DB (via a callback) BEFORE the first click
  click     the board's submit button (BoardProfile.submit_button_js, else FIND_SUBMIT_JS)
  verify    loop, at most settings.submit_max_attempts rounds:
              structured page signals in code (form still there? errors? code inputs?)
              + one driver call returning PostSubmitState
              -> escalated to the vision model with a screenshot when the verdict is
                 inconsistent or contradicts the code signals
            submitted   -> capture the confirmation, done
            email_code  -> fetch the code from Gmail, type it in, submit again
            needs_fix   -> fix the fields (code, then the fix agent), re-gate, submit again
            blocked     -> StageFailed(captcha / login_wall / job_closed / blocked)

Submit is only ever clicked again with affirmative evidence the form is still waiting
(a code prompt or validation errors). A retry that finds a previous attempt only verifies.
"""
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from browser_use.browser import BrowserSession
from browser_use.browser.events import ClickElementEvent
from loguru import logger
from pydantic import BaseModel

from yahbah.agent.boards import BoardProfile, with_notes
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

# The submit button of the form holding most of our keyed fields, tagged data-ja-field="submit-cta".
# Prefers an explicit submit button whose text reads like submitting; the phrase list is a
# tolerated shortcut (a miss fails loudly instead of clicking something else).
FIND_SUBMIT_JS = r"""
(() => {
  const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const name = (e) => (e.innerText || e.value || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  const keyed = [...document.querySelectorAll('[data-ja-field]')];
  const forms = [...document.querySelectorAll('form')];
  const count = (f) => keyed.filter((k) => f.contains(k)).length;
  const scope = forms.sort((a, b) => count(b) - count(a))[0] || document;
  const re = /^(submit( (my |your )?application)?|apply|apply now|send( (my )?application)?|complete( my)? application|finish)$/i;
  const submits = [...scope.querySelectorAll('button[type=submit], input[type=submit], button:not([type])')].filter(vis);
  const buttons = [...scope.querySelectorAll('button, [role=button], input[type=button], input[type=submit]')].filter(vis);
  const el = submits.find((e) => re.test(name(e))) || buttons.find((e) => re.test(name(e)))
    || (submits.length === 1 ? submits[0] : null);
  if (!el) return null;
  el.dataset.jaField = 'submit-cta';
  el.scrollIntoView({ block: 'center' });
  return name(el) || '(submit)';
})()
"""

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

# Is the form still on the page? (Our original field keys are numbers, or os-/ag- prefixed.)
PAGE_SIGNALS_JS = r"""
(() => {
  const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const fields = [...document.querySelectorAll('[data-ja-field]')]
    .filter((e) => /^(\d+|os-\d+|ag-\d+)$/.test(e.dataset.jaField) && (vis(e) || e.type === 'file'));
  const submit = document.querySelector('[data-ja-field="submit-cta"]');
  return JSON.stringify({ url: location.href, form_fields_present: fields.length,
                          submit_button_visible: !!(submit && vis(submit)) });
})()
"""

CONFIRMATION_HTML_JS = r"""
(() => (document.querySelector('main, [role=main]') || document.body).innerHTML.slice(0, 4000))()
"""


class PostSubmitState(BaseModel):
    # reasoning first, so the model reasons before committing to a state
    reasoning: str
    state: Literal["submitted", "needs_fix", "email_code", "blocked"]
    problems: list[str] = []  # needs_fix: what has to be fixed
    blocked_reason: Literal["captcha", "login_wall", "job_closed", "other"] | None = None

    def consistent(self) -> bool:
        if self.state == "needs_fix":
            return bool(self.problems)
        if self.state == "blocked":
            return self.blocked_reason is not None
        return True


@dataclass
class Signals:
    url: str
    form_fields_present: int
    submit_button_visible: bool
    errors: list[dict]
    code_inputs: int


@dataclass
class SubmitResult:
    confirmation_url: str
    confirmation_text: str
    confirmation_html: str


def contradicts(verdict: PostSubmitState, s: Signals) -> bool:
    """A verdict the code signals can't support: worth a second opinion with eyes."""
    if verdict.state == "submitted" and s.form_fields_present and (s.errors or s.code_inputs):
        return True
    if verdict.state == "needs_fix" and not s.form_fields_present:
        return True
    if verdict.state == "email_code" and not s.code_inputs:
        return True
    return False


async def read_signals(session: BrowserSession, board: BoardProfile) -> Signals:
    raw = json.loads(await run_js(session, PAGE_SIGNALS_JS))
    return Signals(
        url=raw["url"],
        form_fields_present=raw["form_fields_present"],
        submit_button_visible=raw["submit_button_visible"],
        errors=await read_errors(session),
        code_inputs=int(await run_js(session, board.code_inputs_js or FIND_CODE_INPUTS_JS) or 0),
    )


async def judge(session: BrowserSession, board: BoardProfile, s: Signals, url_before: str) -> PostSubmitState:
    """Driver verdict on the page after a submit; vision model + screenshot when it doesn't add up."""
    prompt = with_notes(load_prompts_config()["prompts"]["post_submit_judge"], board)
    page_text = (await run_js(session, PAGE_TEXT_JS) or "")[:4000]
    user = (
        f"URL before submit: {url_before}\nURL now: {s.url}\n"
        f"Application form fields still on the page: {s.form_fields_present}\n"
        f"Submit button still visible: {s.submit_button_visible}\n"
        f"Verification-code inputs on the page: {s.code_inputs}\n"
        f"Validation errors: {json.dumps(s.errors, ensure_ascii=False)}\n\n"
        f"PAGE TEXT:\n{page_text}"
    )
    verdict: PostSubmitState | None = None
    try:
        verdict = await OllamaClient("driver").generate_structured(prompt, user, PostSubmitState)
    except LLMError as exc:
        logger.warning(f"[submit] driver verdict failed: {exc}")
    if verdict and verdict.consistent() and not contradicts(verdict, s):
        return verdict

    logger.info(f"[submit] escalating verdict to vision (driver said {verdict.state if verdict else None})")
    shot = await session.take_screenshot(format="jpeg", quality=75)
    try:
        seen = await OllamaClient("vision").generate_structured(prompt, user, PostSubmitState, images=[shot])
    except LLMError as exc:
        if verdict and verdict.consistent():
            return verdict
        raise StageFailed("submit_unverified", f"could not judge the page after submit: {exc}") from exc
    return seen if seen.consistent() else (verdict or seen)


async def click_submit(session: BrowserSession, board: BoardProfile) -> str:
    text = await run_js(session, board.submit_button_js or FIND_SUBMIT_JS)
    node = await node_for_key(session, "submit-cta") if text else None
    if node is None:
        raise StageFailed("submit_button_not_found", "no submit button found on the form")
    ev = session.event_bus.dispatch(ClickElementEvent(node=node))
    await ev
    await ev.event_result(raise_if_any=True, raise_if_none=False)
    logger.info(f"[submit] clicked {text!r}")
    return text


async def enter_code(session: BrowserSession, code: str, n_inputs: int) -> None:
    if n_inputs > 1:  # one box per character
        for i, ch in enumerate(code[:n_inputs]):
            if not await focus_key(session, f"code-{i}"):
                raise StageFailed("email_code_entry", f"code box {i} is not focusable")
            cdp = await session.get_or_create_cdp_session()
            await cdp.cdp_client.send.Input.insertText(params={"text": ch}, session_id=cdp.session_id)
    elif n_inputs == 1:
        if not await focus_key(session, "code-0"):
            raise StageFailed("email_code_entry", "code input is not focusable")
        cdp = await session.get_or_create_cdp_session()
        await cdp.cdp_client.send.Input.insertText(params={"text": code}, session_id=cdp.session_id)
    else:
        raise StageFailed("email_code_entry", "the page asks for a code but no code input was found")


async def current_problems(session: BrowserSession, ctx: FillContext) -> list[str]:
    await close_open_dropdowns(session)
    return problems_for(ctx, await read_values(session, list(ctx.fields)), await read_errors(session))


async def fix_problems(session: BrowserSession, board: BoardProfile, ctx: FillContext, verdict_problems: list[str],
                       errors: list[dict], map_new_fields: MapNewFields | None, beat: Beat) -> None:
    """New fields (mapped from the profile), then fields named in validation errors (code),
    then the fix agent for whatever is still wrong."""
    await fill_new_fields(session, ctx, map_new_fields, beat)
    for key in {e["key"] for e in errors if e.get("key") in ctx.fields}:
        if value := ctx.value_for(key):
            try:
                await write_field(session, ctx, key, value)
            except FillError as exc:
                logger.warning(f"[submit] re-fill [{key}] failed: {exc}")
    remaining = await current_problems(session, ctx)
    if remaining or verdict_problems:
        await fix_with_agent(session, board, ctx, remaining + [f"(seen on the page) {p}" for p in verdict_problems])


async def submit_and_verify(
    session: BrowserSession,
    board: BoardProfile,
    ctx: FillContext,
    email: str,
    already_attempted: bool,
    mark_attempted: Callable[[], Awaitable[None]],
    map_new_fields: MapNewFields | None = None,
    beat: Beat = lambda _: None,
) -> SubmitResult:
    url_before = await session.get_current_page_url()
    submit_epoch = int(time.time())

    if already_attempted:
        logger.warning("[submit] a previous attempt already clicked submit: verifying only")
    else:
        if problems := await current_problems(session, ctx):
            raise StageFailed("fill_failed", "; ".join(problems)[:1000])
        await mark_attempted()
        beat("submit: clicking")
        await click_submit(session, board)

    for attempt in range(1, settings.submit_max_attempts + 1):
        await asyncio.sleep(3)
        s = await read_signals(session, board)
        verdict = await judge(session, board, s, url_before)
        logger.info(f"[submit] round {attempt}: {verdict.state} — {verdict.reasoning[:200]}")
        beat(f"submit: {verdict.state}")

        if verdict.state == "submitted":
            return SubmitResult(
                confirmation_url=s.url,
                confirmation_text=(await run_js(session, PAGE_TEXT_JS) or "")[:2000],
                confirmation_html=await run_js(session, CONFIRMATION_HTML_JS) or "",
            )
        if verdict.state == "blocked":
            reason = verdict.blocked_reason or "other"
            raise StageFailed("blocked" if reason == "other" else reason, verdict.reasoning[:500])
        if verdict.state == "email_code":
            beat("submit: waiting for the email code")
            try:
                code = await GmailClient().fetch_verification_code(
                    email, since_epoch=submit_epoch, subject_hint=board.email_code_subject,
                    code_pattern=board.email_code_pattern)
            except RuntimeError as exc:
                raise StageFailed("email_code_timeout", str(exc)) from exc
            await enter_code(session, code, s.code_inputs)
        else:  # needs_fix
            await fix_problems(session, board, ctx, verdict.problems, s.errors, map_new_fields, beat)
            if still := await current_problems(session, ctx):
                raise StageFailed("fill_failed", "; ".join(still)[:1000])

        # Affirmative evidence the form is still waiting (code prompt / errors fixed): submit again.
        if (await read_signals(session, board)).submit_button_visible:
            await click_submit(session, board)

    raise StageFailed("submit_unverified", f"no confirmation after {settings.submit_max_attempts} rounds")
