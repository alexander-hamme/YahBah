"""
Submit and advance controls: one classifier, used to locate the submit button and to stop agents.

The classifier looks at the DOM element a click (or key press) would activate and returns
  submit   finalises the application ("Submit application", "Apply", a type=submit button of unknown label)
  advance  moves to another step or saves ("Next", "Continue", "Save and continue", "Review")
  other    everything else (inputs, options, "Back", "Attach", links, ...)
A native submit button (type=submit, or a <button> without a type in a form) is never `other`: it is
`advance` when its label reads like a step button, `submit` otherwise. A label alone is not proof of
either; it decides only for controls that carry no structural signal (type=button, role=button, links).

Two users, one definition (the JS below):
  FIND_SUBMIT_JS      the pipeline's own locator: the one control among `submit` that gets clicked, by code,
                      after the gate and the claim. `advance` controls are never clicked as the final submit.
  GuardedTools        the browser-use tools every agent phase runs with. `act()` refuses a click on a
                      control classified submit or advance, and Enter (implicit form submission) or Space on
                      one, before the action reaches the browser. In "navigate" mode (the discover agent
                      clicking "Apply" on a job page) that only applies once the page shows an application
                      form, or the control sits in a form of its own that takes input; in "form" and "fix"
                      modes it always applies and the agent may not navigate away. With a fill plan
                      (`text_allowed`, set by make_tools from the FillContext) it also refuses free-text
                      `input` that is not part of any planned answer (VALUE_NOT_PLANNED).
  check_still_on_form the tripwire after a "fix" agent: if the document was replaced, the URL changed or the
                      form's fields vanished, an unclassified control may have submitted the form
                      (StageFailed "possibly_submitted"; the activity records the claim, the run ends
                      SUBMIT_UNCERTAIN).
Page-level backstop for the fix agent: SUBMIT_EVENT_GUARD_JS cancels native `submit` events whatever
control or key raised them (a label the classifier does not know cannot get past that for native forms).
"""
import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from browser_use import ActionResult, Tools
from browser_use.browser import BrowserSession
from browser_use.browser.events import SwitchTabEvent
from loguru import logger

from yahbah.agent.tools import run_js
from yahbah.llm import logs

AgentMode = Literal["navigate", "form", "fix"]

# ── the classifier (JS library shared by every entry point below) ──────────────

_LIB = r"""
const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
const SUBMIT = /\b(submit|apply|send|finish|finali[sz]e|complete|confirm)\b/i;
const ADVANCE = /\b(next|continue|proceed|save|review|forward|skip)\b/i;
const NOT_ADVANCE = /\b(month|year|week|slide|image|photo|result)s?\b/i;  // calendar / carousel "next"
const NOT_BUTTON = /^(option|menuitem|menuitemradio|menuitemcheckbox|radio|checkbox|tab|switch|combobox|listbox|treeitem|gridcell|textbox|searchbox|slider|spinbutton)$/;
const BUTTONISH = 'button, a, summary, input[type=submit], input[type=button], input[type=image], input[type=reset], [role=button], [role=link], [onclick]';
const PLAIN_INPUT = 'select, textarea, option, input:not([type=submit]):not([type=button]):not([type=image]):not([type=reset])';
const names = (el) => {
  const own = norm(el.tagName === 'INPUT' ? (el.value || el.alt) : el.innerText);
  return [el.getAttribute('aria-label'), own.length <= 80 ? own : '', el.getAttribute('title')].map(norm).filter(Boolean);
};
// The control a click on `start` activates: the nearest button-like ancestor, unless something
// on the way up is an option, radio, checkbox, combobox or plain input (those are not buttons).
const controlOf = (start) => {
  let el = start;
  if (el.tagName === 'LABEL' && el.control) el = el.control;
  for (let i = 0; el && el.nodeType === 1 && i < 8; el = el.parentElement, i++) {
    if (NOT_BUTTON.test((el.getAttribute('role') || '').toLowerCase())) return null;
    if (el.matches(PLAIN_INPUT)) return null;
    if (el.matches(BUTTONISH)) return el;
  }
  return null;
};
// Does typing into it type? (Not a button, checkbox, radio, select, file input, ...: a key press there acts on it.)
const textual = (el) => {
  const t = (el.type || '').toLowerCase();
  return el.tagName === 'TEXTAREA' || !!el.isContentEditable || /^(textbox|searchbox|combobox)$/.test((el.getAttribute('role') || '').toLowerCase())
    || (el.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit', 'reset', 'image', 'file', 'range', 'color', 'hidden'].includes(t));
};
const classify = (start) => {
  const el = controlOf(start);
  if (!el) return { kind: 'other', name: '', structural: false, button: false, textual: textual(start), tag: (start.tagName || '').toLowerCase() };
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  const structural = (tag === 'button' && el.type === 'submit' && !!el.form)
    || (tag === 'input' && (type === 'submit' || type === 'image'));
  const label = names(el).join(' | ');
  let kind = SUBMIT.test(label) ? 'submit' : (ADVANCE.test(label) && !NOT_ADVANCE.test(label)) ? 'advance' : 'other';
  if (structural) kind = kind === 'advance' ? 'advance' : 'submit';
  return { kind, name: names(el)[0] || '', structural, button: true, textual: textual(el), tag };
};
const controls = () => [...document.querySelectorAll('input, select, textarea, [role=combobox]')].filter((e) => {
  const t = (e.type || '').toLowerCase();
  if (['hidden', 'submit', 'button', 'reset', 'image'].includes(t)) return false;
  if (e.getAttribute('aria-hidden') === 'true') return false;
  return t === 'file' || vis(e);
});
const typed = (list) => list.filter((e) => !['checkbox', 'radio'].includes((e.type || '').toLowerCase()));
// Mirrors discover.looks_like_form: three or more fillable controls (not counting radios and checkboxes), or a file input.
const formPage = () => {
  const c = typed(controls());
  return c.length >= 3 || c.some((e) => e.type === 'file');
};
// Is `el` inside a <form> of its own that takes input (two fillable controls, or a file)? A small form still counts
// where the page as a whole doesn't look like an application: a "Subscribe" form somewhere else doesn't make a job
// page's "Apply" link a submit control, but a two-field quick-apply form's own buttons are.
const inForm = (el) => {
  const f = el.form || el.closest('form');
  if (!f) return false;
  const c = typed(controls().filter((e) => f.contains(e)));
  return c.length >= 2 || c.some((e) => e.type === 'file');
};
"""

# Runtime.callFunctionOn with the clicked element as `this`.
CLASSIFY_THIS_JS = "function() {" + _LIB + "return JSON.stringify({ ...classify(this), form_page: formPage() || inForm(this) }); }"

# The focused element, for key presses.
ACTIVE_INFO_JS = "(() => {" + _LIB + r"""
  // The focused element, through shadow roots and same-origin frames. Focus in a frame we can't read: unknown.
  let a = document.activeElement, unknown = false;
  for (let i = 0; a && i < 10; i++) {
    if (a.shadowRoot && a.shadowRoot.activeElement) { a = a.shadowRoot.activeElement; continue; }
    if (a.tagName === 'IFRAME' || a.tagName === 'FRAME') {
      let d = null;
      try { d = a.contentDocument; } catch (e) { d = null; }
      if (!d) { unknown = true; break; }
      if (d.activeElement) { a = d.activeElement; continue; }
    }
    break;
  }
  if (!a || a === document.body || a === document.documentElement) {
    return JSON.stringify({ kind: 'other', tag: 'body', button: false, textual: false, editable: false, expanded: false,
                            unknown, form_page: formPage() });
  }
  return JSON.stringify({ ...classify(a), editable: a.tagName === 'TEXTAREA' || a.isContentEditable,
                          expanded: a.getAttribute('aria-expanded') === 'true', unknown,
                          form_page: formPage() || inForm(a) });
})()"""

FORM_PAGE_JS = "(() => {" + _LIB + "return formPage(); })()"

# The pipeline's own locator. The submit button of the form holding most of our keyed fields (the whole
# page when no form holds one), tagged data-ja-field="submit-cta". Among controls classified `submit`:
# one whose whole label reads like submitting wins (a native submit button first), else the only one.
# Returns {text, kind, disabled, found} (text null: nothing to click; found lists the submit and advance
# controls seen, for the error message). Disabled controls are only chosen when no enabled one exists.
FIND_SUBMIT_JS = "(() => {" + _LIB + r"""
  const keyed = [...document.querySelectorAll('[data-ja-field]')].filter((k) => /^(\d+|os-\d+|ag-\d+)$/.test(k.dataset.jaField));
  const count = (f) => keyed.filter((k) => f.contains(k)).length;
  const best = [...document.querySelectorAll('form')].sort((a, b) => count(b) - count(a))[0];
  const scope = best && count(best) ? best : null;
  const FINAL = /^(submit( (my |your )?application)?|apply|apply now|send( (my )?application)?|complete( my)? application|finish)$/i;
  const cands = [...document.querySelectorAll('button, [role=button], input[type=button], input[type=submit], input[type=image]')]
    .filter((c) => vis(c) && (!scope || scope.contains(c) || c.form === scope))
    .map((el) => ({ el, ...classify(el), disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true' }));
  const choose = (list) => {
    const strong = list.filter((c) => FINAL.test(norm(c.name)));
    return strong.find((c) => c.structural) || strong[0] || (list.length === 1 ? list[0] : null);
  };
  const submits = cands.filter((c) => c.kind === 'submit');
  const pick = choose(submits.filter((c) => !c.disabled)) || choose(submits.filter((c) => c.disabled));
  const found = cands.filter((c) => c.kind !== 'other').map((c) => `${c.kind}: ${c.name || '(no label)'}`);
  for (const old of document.querySelectorAll('[data-ja-field="submit-cta"]')) delete old.dataset.jaField;
  if (!pick) return JSON.stringify({ text: null, found });
  pick.el.dataset.jaField = 'submit-cta';
  pick.el.scrollIntoView({ block: 'center' });
  return JSON.stringify({ text: pick.name || '(submit)', kind: pick.kind, disabled: pick.disabled, found });
})()"""

# What the page shows now, for judging a submit: the document it is (the stamp survives only in the document
# we started on), how many fillable controls are left, our submit control (tagged data-ja-field="submit-cta"),
# a spinner / aria-busy, and the visible text of the main content.
PAGE_EVIDENCE_JS = "(() => {" + _LIB + r"""
  const cta = document.querySelector('[data-ja-field="submit-cta"]');
  const root = document.querySelector('main, [role=main]') || document.body;
  const here = !!(cta && cta.isConnected);
  return JSON.stringify({
    url: location.href, token: window.__jaDocToken || null, controls: controls().length,
    cta: { present: here, visible: here && vis(cta), disabled: here && !!(cta.disabled || cta.getAttribute('aria-disabled') === 'true') },
    busy: [...document.querySelectorAll('[aria-busy="true"], [role=progressbar], [class*="spinner" i]')].some(vis),
    title: document.title, text: norm(root.innerText).slice(0, 6000) });
})()"""

# Page-level backstop while a fix agent works: cancel native submit events in the capture phase at window
# level, before the page's own handlers, whichever control or key raised them. on=false removes it.
SUBMIT_EVENT_GUARD_JS = r"""
((on) => {
  window.__jaBlockSubmit = window.__jaBlockSubmit || ((e) => { e.preventDefault(); e.stopImmediatePropagation(); });
  if (on) window.addEventListener('submit', window.__jaBlockSubmit, true);
  else window.removeEventListener('submit', window.__jaBlockSubmit, true);
  return true;
})
"""


async def block_native_submits(session: BrowserSession, on: bool) -> None:
    try:
        await run_js(session, f"{SUBMIT_EVENT_GUARD_JS}({json.dumps(on)})")
    except Exception:
        if on:
            raise  # no guard, no agent
        # off: the page may have navigated under the agent; its document is gone with the listener


# ── what a key press can do ───────────────────────────────────────────────────

_ENTER = {"enter", "return", "numpadenter"}
_MODIFIERS = {"ctrl", "control", "cmd", "command", "meta", "alt", "option", "shift"}


@dataclass(frozen=True)
class Keys:
    enter: bool     # Enter, or text containing a newline
    modified: bool  # Enter with ctrl/cmd/alt: "send" in many apps
    space: bool     # Space, or text containing one: activates a focused button


def parse_keys(keys: str) -> Keys:
    """Which keys a send_keys string presses, as browser-use reads it: a combination (`Control+Enter`),
    a named key (`Enter`, `Space`), or text typed character by character (a newline is Enter)."""
    if "\n" in keys or "\r" in keys:
        return Keys(enter=True, modified=False, space=" " in keys)
    if "+" in keys and keys != "+":
        prefix, _, main = keys.rpartition("+")
        mods = [m.strip().lower() for m in prefix.split("+")]
        if all(m in _MODIFIERS for m in mods):
            main = main.strip().lower()
            return Keys(enter=main in _ENTER, modified=any(m != "shift" for m in mods),
                        space=main in ("space", " "))
    low = keys.strip().lower()
    if low in _ENTER:
        return Keys(enter=True, modified=False, space=False)
    return Keys(enter=False, modified=False, space=low == "space" or " " in keys)


# ── the guard ─────────────────────────────────────────────────────────────────

BLOCKED = ("Blocked: this would activate a control that submits or advances the application. Only the "
           "pipeline's own code may do that. Fix fields with fill_field / upload_document instead, or finish.")
FORM_PHASE_NO_NAVIGATION = ("Blocked: leave the page alone. This step works on the form that is open; "
                            "navigating away is not allowed.")
VALUE_NOT_PLANNED = ("Refused: that text is not part of any planned answer. Use fill_field with the planned value, "
                     "or report the problem as unresolved.")
_NAVIGATION_ACTIONS = {"navigate", "search", "go_back", "close"}


async def classify_node(session: BrowserSession, node) -> dict:
    """The classifier's verdict for a browser-use DOM node: {kind, name, structural, tag, form_page}."""
    cdp = await session.cdp_client_for_node(node)
    resolved = await cdp.cdp_client.send.DOM.resolveNode(
        params={"backendNodeId": node.backend_node_id}, session_id=cdp.session_id)
    result = await cdp.cdp_client.send.Runtime.callFunctionOn(
        params={"functionDeclaration": CLASSIFY_THIS_JS, "objectId": resolved["object"]["objectId"],
                "returnByValue": True},
        session_id=cdp.session_id)
    if result.get("exceptionDetails"):
        raise RuntimeError(result["exceptionDetails"].get("text") or "classification failed")
    return json.loads(result["result"]["value"])


def _gated(mode: AgentMode, form_page: bool) -> bool:
    """Do submit/advance controls count as off limits right now? Always for agents working on the form;
    for the navigating agent only once the page shows an application form."""
    return mode != "navigate" or form_page


async def refusal_for(mode: AgentMode, session: BrowserSession, action_name: str, params: dict) -> str | None:
    """Why this agent action must not run (a message for the agent), or None."""
    strict = mode != "navigate"
    if action_name in _NAVIGATION_ACTIONS and strict:
        return FORM_PHASE_NO_NAVIGATION

    if action_name == "click":
        if params.get("coordinate_x") is not None or params.get("index") is None:
            # a click at a point can hit anything: only the navigating agent on a page without a form
            return None if mode == "navigate" and not await run_js(session, FORM_PAGE_JS) else BLOCKED
        node = await session.get_element_by_index(params["index"])
        if node is None:
            return None  # the tool reports a stale index itself
        try:
            info = await classify_node(session, node)
        except Exception as exc:
            logger.warning(f"[guard] could not classify element {params['index']}: {exc}")
            return BLOCKED if strict else None
        if info["kind"] in ("submit", "advance") and _gated(mode, info["form_page"]):
            logger.warning(f"[guard] refused click on {info['kind']} control {info['name']!r}")
            return BLOCKED
        return None

    if action_name == "send_keys":
        keys = parse_keys(params.get("keys") or "")
        if not (keys.enter or keys.space):
            return None
        try:
            active = json.loads(await run_js(session, ACTIVE_INFO_JS))
        except Exception as exc:
            logger.warning(f"[guard] could not read the focused element: {exc}")
            return BLOCKED if strict else None
        if not _gated(mode, active["form_page"]):
            return None
        if active.get("unknown"):
            return BLOCKED  # focus is in a frame we can't inspect: fail closed
        if active["kind"] in ("submit", "advance"):
            return BLOCKED  # Enter or Space on a focused button clicks it
        if keys.enter and (keys.modified or not (active["editable"] or active["expanded"])):
            # implicit form submission; Enter in a textarea or in an open dropdown is not
            return BLOCKED
        return None

    if action_name == "input":
        # browser-use focuses the target and sends real key presses: a space typed into a button clicks it.
        # So the target is classified like a click target, in every mode, and must be something that takes text.
        text = params.get("text") or ""
        index = params.get("index") or 0
        try:
            node = await session.get_element_by_index(index) if index else None
            if node is not None and getattr(node, "backend_node_id", 0):
                info = await classify_node(session, node)
            else:  # typed into whatever has focus
                info = json.loads(await run_js(session, ACTIVE_INFO_JS))
                if info.get("unknown"):
                    return BLOCKED
        except Exception as exc:
            logger.warning(f"[guard] could not classify the input target: {exc}")
            return BLOCKED
        if info["button"] or info["kind"] in ("submit", "advance") or not info["textual"]:
            return BLOCKED
        if ("\n" in text or "\r" in text) and info["tag"] != "textarea" and (strict or info["form_page"]):
            return BLOCKED  # typed as Enter
    return None


class GuardedTools(Tools):
    """browser-use Tools whose `act` runs the guard first. A refusal comes back to the agent as an error
    result (it can try something else); `mode` is set by run_agent for the phase. `text_allowed`, set by
    make_tools for agents working on a planned fill, refuses free-text `input` that is in no planned answer."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mode: AgentMode = "form"  # the safe default: a phase that forgets to say is fully guarded
        self.text_allowed: Callable[[str], bool] | None = None

    async def act(self, action, browser_session, *args, **kwargs):
        for name, params in action.model_dump(exclude_unset=True).items():
            if params is None:
                continue
            p = params if isinstance(params, dict) else {}
            reason = await refusal_for(self.mode, browser_session, name, p)
            if (not reason and name == "input" and self.text_allowed is not None
                    and not self.text_allowed(p.get("text") or "")):
                reason = VALUE_NOT_PLANNED
            if reason:
                logs.append("guard.jsonl", {"mode": self.mode, "action": name, "params": params, "refused": reason[:60]})
                return ActionResult(error=reason)
            break  # one action per call
        return await super().act(action, browser_session, *args, **kwargs)


# ── the tripwire ──────────────────────────────────────────────────────────────

SNAPSHOT_JS = "(() => {" + _LIB + r"""
  window.__jaDocToken = window.__jaDocToken || (Math.random().toString(36).slice(2) + Date.now().toString(36));
  return JSON.stringify({ url: location.href, token: window.__jaDocToken, controls: controls().length });
})()"""


@dataclass(frozen=True)
class PageSnapshot:
    target_id: str | None
    url: str
    token: str | None   # stamped on the document: a replaced document (navigation, reload) has none
    controls: int       # visible fillable controls


async def snapshot(session: BrowserSession) -> PageSnapshot:
    raw = json.loads(await run_js(session, SNAPSHOT_JS))
    return PageSnapshot(session.agent_focus_target_id, raw["url"], raw["token"], raw["controls"])


def _same_page(a: str, b: str) -> bool:
    return a.split("#")[0] == b.split("#")[0]


async def _departure(session: BrowserSession, before: PageSnapshot) -> str | None:
    """How the page differs from the form we started on, or None while it is still that form."""
    if before.target_id:
        if before.target_id not in {t.target_id for t in await session.get_tabs()}:
            return "the form's tab was closed"
        if session.agent_focus_target_id != before.target_id:  # e.g. a link opened a tab: back to the form
            await session.event_bus.dispatch(SwitchTabEvent(target_id=before.target_id))
    try:
        now = await snapshot(session)
    except Exception as exc:  # mid-navigation, or the page is gone
        return f"the page cannot be read ({type(exc).__name__})"
    if now.token != before.token:
        return "the document was replaced"
    if not _same_page(now.url, before.url):
        return f"the URL changed to {now.url[:100]}"
    if before.controls and not now.controls:
        return "the form's fields are gone"
    return None


async def check_still_on_form(session: BrowserSession, before: PageSnapshot, settle_seconds: float = 3.0) -> None:
    """After an agent worked on the filled form: it must still be the same document, URL and form.
    Waits (bounded) for a page that is mid-transition to settle. Otherwise something the agent did may have
    submitted the form: StageFailed("possibly_submitted")."""
    from yahbah.agent.discover import StageFailed  # discover imports the agent runner, which imports this module

    loop = asyncio.get_running_loop()
    deadline = loop.time() + settle_seconds
    while (departed := await _departure(session, before)) is not None:
        if loop.time() >= deadline:
            raise StageFailed("possibly_submitted", f"an agent action left the form ({departed}); the "
                                                    "application may have been submitted")
        await asyncio.sleep(0.3)
