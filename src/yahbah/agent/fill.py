"""
Code-first form filling, addressed by the data-ja-field keys that map_form stamped on each field.

fill_form():
  1. every mapped value is written by a per-type executor (text, native select, custom combobox,
     radio/checkbox, file); a model is only asked when an option can't be matched in code
  2. fields that appear only after answering others ("If yes, please explain") are mapped and filled
  3. values are read back and diffed against what we meant to write; mismatches are retried once
  4. remaining problems go to a fix agent with key-based fill tools — never a submit tool

Widget facts (from the spike, verified on Greenhouse but common everywhere):
- React-select: options don't exist until the menu opens; the input is a few px wide; typing filters;
  Tab selects the highlighted option, so we never use Tab.
- File inputs are hidden behind "Attach" buttons: set files on the <input type=file> directly via CDP.
- intl-tel-input (.iti) phone fields carry their own country picker: type the national number only.
"""
import asyncio
import json
import os
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from browser_use import ActionResult, Tools
from browser_use.browser import BrowserSession
from browser_use.browser.events import ClickCoordinateEvent, SendKeysEvent
from loguru import logger
from pydantic import BaseModel

from yahbah.agent.boards import BoardProfile, with_notes
from yahbah.agent.tools import (
    READ_OPEN_OPTIONS_JS,
    READ_OWN_OPTIONS_JS,
    close_open_dropdowns,
    focus_key,
    get_form_schema,
    run_js,
)
from yahbah.config import load_prompts_config
from yahbah.llm.client import LLMError, OllamaClient

# ── JS ────────────────────────────────────────────────────────────────────────

_EL = "document.querySelector(`[data-ja-field=\"${key}\"]`)"

FIELD_KIND_JS = r"""
((key) => {
  const el = document.querySelector(`[data-ja-field="${key}"]`);
  if (!el) return null;
  return JSON.stringify({ tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
                          role: el.getAttribute('role') || '', iti: !!el.closest('.iti') });
})
"""

# Current value of each keyed field, as a person would read it off the page.
READ_VALUES_JS = r"""
((keys) => {
  const txt = (el) => (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
  // What a widget displays, without screen-reader announcements (live regions such as React-select's
  // transient "option Yes, selected."): those aren't its value.
  const shown = (el) => {
    const clone = el.cloneNode(true);
    clone.querySelectorAll('[aria-live], [role=status], [role=log], [role=alert]').forEach((n) => n.remove());
    return (clone.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const labelOf = (el) => (el.labels?.length ? txt(el.labels[0]) : '') || el.getAttribute('aria-label') || el.value || '';
  // Nearest ancestor holding no other form control: where a custom widget shows its value.
  const controls = 'input:not([type=hidden]):not([aria-hidden=true]), select, textarea, [role=combobox]';
  const container = (el) => {
    let best = el.parentElement || el;
    for (let a = el.parentElement, i = 0; a && i < 6; a = a.parentElement, i++) {
      if ([...a.querySelectorAll(controls)].some((c) => c !== el && !el.contains(c) && !c.contains(el))) break;
      best = a;
    }
    return best;
  };
  const out = {};
  for (const key of keys) {
    const el = document.querySelector(`[data-ja-field="${key}"]`);
    if (!el) {
      // Some widgets replace the input after use (e.g. an upload swapped for the file name): look at
      // the container tagged before we used it.
      const group = document.querySelector(`[data-ja-group="${key}"]`);
      out[key] = { missing: true, value: '', shown: group ? shown(group).slice(0, 300) : '' };
      continue;
    }
    const type = (el.type || '').toLowerCase();
    if (el.tagName === 'SELECT') {
      out[key] = { value: el.selectedOptions[0] ? el.selectedOptions[0].text.trim() : '' };
    } else if (type === 'file') {
      out[key] = { value: [...(el.files || [])].map((f) => f.name).join(', '), files: (el.files || []).length,
                   shown: shown(container(el)).slice(0, 300) };
    } else if (type === 'radio' || type === 'checkbox') {
      const group = el.name ? [...document.getElementsByName(el.name)] : [el];
      out[key] = { value: group.filter((g) => g.checked).map(labelOf).join(', '), checked: group.some((g) => g.checked) };
    } else if (el.getAttribute('role') === 'combobox') {
      out[key] = { value: shown(container(el)).slice(0, 300) };
    } else {
      out[key] = { value: el.value ?? txt(el) };
    }
  }
  return JSON.stringify(out);
})
"""

# Tag the field's container (nearest ancestor holding no other form control) data-ja-group=<key>,
# so its state can still be read if the widget replaces the input itself.
TAG_GROUP_JS = r"""
((key) => {
  const el = document.querySelector(`[data-ja-field="${key}"]`);
  if (!el) return false;
  const controls = 'input:not([type=hidden]):not([aria-hidden=true]), select, textarea, [role=combobox]';
  let best = el.parentElement || el;
  for (let a = el.parentElement, i = 0; a && i < 6; a = a.parentElement, i++) {
    if ([...a.querySelectorAll(controls)].some((c) => c !== el && !el.contains(c) && !c.contains(el))) break;
    best = a;
  }
  best.dataset.jaGroup = key;
  return true;
})
"""

# Values the browser can't type (date/time inputs, native selects): set through the native setter so
# React-controlled elements notice, then fire input + change.
SET_NATIVE_VALUE_JS = r"""
((key, value, byOptionText) => {
  const el = document.querySelector(`[data-ja-field="${key}"]`);
  if (!el) return false;
  let v = value;
  if (byOptionText) {
    const opt = [...el.options].find((o) => o.text.trim() === value);
    if (!opt) return false;
    v = opt.value;
  }
  const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})
"""

# Centre of a visible option (scoped to this combobox like READ_OWN_OPTIONS_JS, else any visible
# option), scrolled into view, for a real mouse click.
OPTION_POINT_JS = r"""
((key, text) => {
  const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const txt = (o) => (o.innerText || o.textContent || '').replace(/\s+/g, ' ').trim();
  const el = document.querySelector(`[data-ja-field="${key}"]`);
  let opts = [];
  if (el) {
    for (const attr of ['aria-controls', 'aria-owns']) {
      for (const id of (el.getAttribute(attr) || '').split(/\s+/).filter(Boolean)) {
        opts.push(...(document.getElementById(id)?.querySelectorAll('[role=option]') || []));
      }
    }
  }
  if (!opts.length) opts = [...document.querySelectorAll('[role=listbox] [role=option], [role=option], [class*="menu"] [class*="option"]')];
  const hit = opts.filter(vis).find((o) => txt(o) === text);
  if (!hit) return null;
  hit.scrollIntoView({ block: 'nearest' });
  const r = hit.getBoundingClientRect();
  return JSON.stringify({ x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) });
})
"""

# Centre of the radio/checkbox in the keyed field's group whose label is `text` (or of its label,
# when the input itself is visually hidden), for a real mouse click.
CHOICE_POINT_JS = r"""
((key, text) => {
  const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const txt = (el) => (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
  const labelOf = (el) => (el.labels?.length ? txt(el.labels[0]) : '') || el.getAttribute('aria-label') || el.value || '';
  const el = document.querySelector(`[data-ja-field="${key}"]`);
  if (!el) return null;
  const group = el.name ? [...document.getElementsByName(el.name)] : [el];
  const input = text === null ? el : group.find((g) => labelOf(g) === text);
  if (!input) return null;
  const target = vis(input) ? input : (input.labels?.[0] || input.closest('label'));
  if (!target || !vis(target)) return null;
  target.scrollIntoView({ block: 'center' });
  const r = target.getBoundingClientRect();
  return JSON.stringify({ x: Math.round(r.left + Math.min(r.width / 2, 12)), y: Math.round(r.top + r.height / 2),
                          checked: input.checked });
})
"""

GROUP_OPTIONS_JS = r"""
((key) => {
  const txt = (el) => (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
  const labelOf = (el) => (el.labels?.length ? txt(el.labels[0]) : '') || el.getAttribute('aria-label') || el.value || '';
  const el = document.querySelector(`[data-ja-field="${key}"]`);
  if (!el) return '[]';
  const group = el.name ? [...document.getElementsByName(el.name)] : [el];
  return JSON.stringify(group.map(labelOf));
})
"""

# While an agent works on the form, it must not be able to submit it — not even with the generic
# click tool or an Enter key press. on=true: disable explicit submit buttons and cancel any form
# submission in the capture phase at window level (before the page's own handlers). on=false: undo.
GUARD_SUBMIT_JS = r"""
((on) => {
  if (on) {
    window.__jaBlockSubmit = window.__jaBlockSubmit || ((e) => { e.preventDefault(); e.stopImmediatePropagation(); });
    window.addEventListener('submit', window.__jaBlockSubmit, true);
    for (const b of document.querySelectorAll('form button[type=submit], form input[type=submit], [data-ja-field="submit-cta"]')) {
      if (!b.disabled) { b.disabled = true; b.dataset.jaGuarded = '1'; }
    }
  } else {
    if (window.__jaBlockSubmit) window.removeEventListener('submit', window.__jaBlockSubmit, true);
    for (const b of document.querySelectorAll('[data-ja-guarded]')) { b.disabled = false; delete b.dataset.jaGuarded; }
  }
  return true;
})
"""

# Validation errors: invalid fields (with the error text tied to them) plus visible alerts.
READ_ERRORS_JS = r"""
(() => {
  const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const txt = (el) => (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const seen = new Set();
  const add = (key, message) => {
    const id = `${key}|${message}`;
    if (!seen.has(id) && (key || message)) { seen.add(id); out.push({ key, message: message.slice(0, 200) }); }
  };
  for (const el of document.querySelectorAll('[aria-invalid=true]')) {
    const ids = [el.getAttribute('aria-describedby'), el.getAttribute('aria-errormessage')]
      .filter(Boolean).join(' ').split(/\s+/).filter(Boolean);
    add(el.dataset.jaField || null, ids.map((id) => txt(document.getElementById(id))).filter(Boolean).join(' '));
  }
  for (const el of document.querySelectorAll('[role=alert], [aria-live=assertive], [class*="error" i]')) {
    const t = txt(el);
    if (vis(el) && t && t.length <= 200 && !el.querySelector('input, select, textarea')) {
      const owner = el.closest('div, fieldset, li')?.querySelector('[data-ja-field]');
      add(owner?.dataset.jaField || null, t);
    }
  }
  return JSON.stringify(out);
})()
"""

# ── Option matching ───────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


def match_option(value: str, options: list[str]) -> str | None:
    """The option meaning `value`, decided in code: exact, then a unique prefix, then a unique
    whole-word match. None when ambiguous (a model decides then)."""
    v = _norm(value)
    if not v:
        return None
    options = [o for o in options if o and o != "..."]
    for candidates in (
        [o for o in options if _norm(o) == v],
        [o for o in options if _norm(o).startswith(v) or (_norm(o) and v.startswith(_norm(o)))],
        [o for o in options if re.search(rf"\b{re.escape(v)}\b", _norm(o))],
    ):
        if len(candidates) == 1 or (candidates and all(_norm(c) == v for c in candidates)):
            return candidates[0]
    return None


class _OptionPick(BaseModel):
    reasoning: str
    choice: int | None  # 1-based index into the options; null if none fits


async def pick_option(label: str, value: str, options: list[str]) -> str | None:
    """Code first (match_option), then one structured driver call."""
    options = [o for o in options if o and o != "..."]
    if not options:
        return None
    if found := match_option(value, options):
        return found
    listing = "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1))
    try:
        pick = await OllamaClient("driver").generate_structured(
            system_prompt=load_prompts_config()["prompts"]["option_pick"],
            user_prompt=f"QUESTION: {label}\nINTENDED ANSWER: {value}\n\nOPTIONS:\n{listing}",
            response_model=_OptionPick,
        )
    except LLMError as exc:
        logger.warning(f"[fill] option pick failed for {label!r}: {exc}")
        return None
    if pick.choice and 1 <= pick.choice <= len(options):
        return options[pick.choice - 1]
    return None


# ── Executors ─────────────────────────────────────────────────────────────────

class FillError(Exception):
    pass


async def _insert_text(session: BrowserSession, text: str) -> None:
    """Type into the focused element like an IME commit: fires beforeinput/input, so React notices."""
    cdp = await session.get_or_create_cdp_session()
    await cdp.cdp_client.send.Input.insertText(params={"text": text}, session_id=cdp.session_id)


async def _click_point(session: BrowserSession, point_json: str | None) -> bool:
    if not point_json:
        return False
    p = json.loads(point_json)
    await session.event_bus.dispatch(ClickCoordinateEvent(coordinate_x=p["x"], coordinate_y=p["y"]))
    await _pause()
    return True


async def _pause(secs: float = 0.3) -> None:
    await asyncio.sleep(secs)


async def fill_text(session: BrowserSession, key: str, value: str) -> str:
    """Write text into an input/textarea; returns what was written."""
    kind = json.loads(await run_js(session, f"{FIELD_KIND_JS}({json.dumps(key)})") or "null")
    if kind is None:
        raise FillError(f"field {key} is no longer on the page")
    if kind["type"] == "tel" and kind["iti"]:
        value = re.sub(r"^\+\d{1,3}[\s.-]*", "", value)  # the widget's own picker supplies the country code
    if kind["type"] in ("date", "month", "time", "datetime-local", "week", "range", "color"):
        if not await run_js(session, f"{SET_NATIVE_VALUE_JS}({json.dumps(key)}, {json.dumps(value)}, false)"):
            raise FillError(f"could not set {kind['type']} value on field {key}")
        return value
    if not await focus_key(session, key):
        raise FillError(f"could not focus field {key}")
    await run_js(session, f"((key) => {{ const el = {_EL}; el.select && el.select(); }})({json.dumps(key)})")
    await _insert_text(session, value)
    await run_js(session, f"((key) => {{ {_EL}?.blur(); }})({json.dumps(key)})")
    return value


async def select_native(session: BrowserSession, key: str, label: str, value: str) -> str:
    options = json.loads(await run_js(
        session, f"((key) => JSON.stringify([...{_EL}.options].map((o) => o.text.trim())))({json.dumps(key)})"))
    chosen = await pick_option(label, value, options)
    if chosen is None:
        raise FillError(f"no option of {label!r} fits {value!r}")
    if not await run_js(session, f"{SET_NATIVE_VALUE_JS}({json.dumps(key)}, {json.dumps(chosen)}, true)"):
        raise FillError(f"could not select {chosen!r} in {label!r}")
    return chosen


async def _visible_options(session: BrowserSession, key: str) -> list[str]:
    for _ in range(6):  # up to ~1.5 s for the menu to render
        await _pause(0.25)
        data = json.loads(await run_js(session, f"{READ_OWN_OPTIONS_JS}({json.dumps(key)})"))
        if not data["total"]:
            data = json.loads(await run_js(session, READ_OPEN_OPTIONS_JS))
        if data["total"]:
            return data["options"]
    return []


async def choose_combobox(session: BrowserSession, key: str, label: str, value: str) -> str:
    """Custom dropdown (React-select & co.): type to filter, pick an option, click it for real."""
    queries = [value] + ([value.split()[0]] if len(value.split()) > 1 else [])
    for query in queries + [""]:
        if not await focus_key(session, key):
            raise FillError(f"could not focus dropdown {label!r}")
        await run_js(session, f"((key) => {{ const el = {_EL}; el.select && el.select(); }})({json.dumps(key)})")
        if query:
            await _insert_text(session, query)
        else:  # nothing matched the typed text: clear it and open the full list
            await session.event_bus.dispatch(SendKeysEvent(keys="Backspace"))
            await session.event_bus.dispatch(SendKeysEvent(keys="ArrowDown"))
        options = await _visible_options(session, key)
        if not options:
            continue
        chosen = await pick_option(label, value, options)
        if chosen and await _click_point(session, await run_js(
                session, f"{OPTION_POINT_JS}({json.dumps(key)}, {json.dumps(chosen)})")):
            await close_open_dropdowns(session)
            return chosen
    await close_open_dropdowns(session)
    raise FillError(f"no option of dropdown {label!r} fits {value!r}")


async def choose_in_group(session: BrowserSession, key: str, label: str, value: str) -> str:
    """Radio group (or checkbox group): click the option whose label fits `value`."""
    options = json.loads(await run_js(session, f"{GROUP_OPTIONS_JS}({json.dumps(key)})"))
    chosen = await pick_option(label, value, options)
    if chosen is None:
        raise FillError(f"no option of {label!r} fits {value!r}")
    point = await run_js(session, f"{CHOICE_POINT_JS}({json.dumps(key)}, {json.dumps(chosen)})")
    if point and json.loads(point)["checked"]:
        return chosen
    if not await _click_point(session, point):
        raise FillError(f"could not click option {chosen!r} of {label!r}")
    return chosen


_TRUE = {"yes", "true", "1", "checked", "agree", "i agree", "accept", "y", "on"}


async def set_checkbox(session: BrowserSession, key: str, value: str) -> str:
    want = _norm(value) in _TRUE
    point = await run_js(session, f"{CHOICE_POINT_JS}({json.dumps(key)}, null)")
    if not point:
        raise FillError(f"checkbox {key} is not clickable")
    if json.loads(point)["checked"] != want:
        await _click_point(session, point)
    return "checked" if want else "unchecked"


async def upload_file(session: BrowserSession, key: str, path: str) -> str:
    """Set the file on the <input type=file> directly (never click "Attach": it opens a native dialog)."""
    if not os.path.isfile(path):
        raise FillError(f"file not found: {path}")
    await run_js(session, f"{TAG_GROUP_JS}({json.dumps(key)})")
    cdp = await session.get_or_create_cdp_session()
    res = await cdp.cdp_client.send.Runtime.evaluate(
        params={"expression": f"document.querySelector('[data-ja-field={json.dumps(key)}]')", "returnByValue": False},
        session_id=cdp.session_id,
    )
    object_id = res.get("result", {}).get("objectId")
    if not object_id:
        raise FillError(f"file field {key} is no longer on the page")
    await cdp.cdp_client.send.DOM.setFileInputFiles(
        params={"files": [os.path.abspath(path)], "objectId": object_id}, session_id=cdp.session_id)
    await _pause(1.0)  # upload widgets read the file asynchronously
    return os.path.basename(path)


# ── Orchestration ─────────────────────────────────────────────────────────────

@dataclass
class FillContext:
    """Everything the executors (and the fix agent's tools) need for one form."""

    fields: dict[str, dict]       # key -> {label, type, required, options}
    mappings: dict[str, dict]     # key -> FieldMapping dict {key, label, field_type, source, value}
    files: dict[str, str]         # "resume" / "cover_letter" -> absolute path
    cover_letter_text: str | None = None
    written: dict[str, str] = field(default_factory=dict)  # key -> what we actually wrote/chose

    def value_for(self, key: str) -> str | None:
        m = self.mappings.get(key)
        if not m or m["source"] == "skip":
            return None
        if m["source"] in ("resume", "cover_letter"):
            if self.fields.get(key, {}).get("type") == "file":
                return self.files.get(m["source"])
            return self.cover_letter_text if m["source"] == "cover_letter" else None
        return m["value"]


async def write_field(session: BrowserSession, ctx: FillContext, key: str, value: str) -> str:
    """Dispatch to the executor for the field's type; records and returns what was written."""
    f = ctx.fields.get(key) or {"label": key, "type": "text", "options": []}
    ftype, label = f["type"], f["label"]
    if ftype == "file":
        written = await upload_file(session, key, value)
    elif ftype == "select":
        written = await select_native(session, key, label, value)
    elif ftype == "combobox":
        written = await choose_combobox(session, key, label, value)
    elif ftype == "radio" or (ftype == "checkbox" and len(f.get("options") or []) > 1):
        written = await choose_in_group(session, key, label, value)
    elif ftype == "checkbox":
        written = await set_checkbox(session, key, value)
    else:
        written = await fill_text(session, key, value)
    ctx.written[key] = written
    return written


async def read_values(session: BrowserSession, keys: list[str]) -> dict[str, dict]:
    return json.loads(await run_js(session, f"{READ_VALUES_JS}({json.dumps(keys)})"))


async def read_errors(session: BrowserSession) -> list[dict]:
    return json.loads(await run_js(session, READ_ERRORS_JS))


def _tokens(s: str) -> Counter:
    return Counter(_norm(s).split())


def value_ok(f: dict, written: str, actual: dict) -> bool:
    """Does the page now show what we wrote? (f: the field, with its type and empty_value)"""
    ftype = f.get("type")
    want = _norm(written)
    if ftype == "file":
        # The input may be gone (widgets swap it for the file name): its tagged container still shows it.
        return actual.get("files", 0) > 0 or (bool(want) and want in _norm(actual.get("shown") or ""))
    if actual.get("missing"):
        return False
    if ftype == "checkbox" and written in ("checked", "unchecked"):
        return actual.get("checked") == (written == "checked")
    if ftype == "combobox" and f.get("empty_value") is not None:
        # The words the widget shows now beyond what it showed while empty (counted, so a chosen "No"
        # still counts when the label says "(Yes/No)") must all belong to the chosen option:
        # "+1" for "United States +1" passes; an untouched "Select..." or another option fails.
        new = _tokens(actual.get("value") or "") - _tokens(f["empty_value"])
        return bool(new) and not (new - _tokens(written))
    got = _norm(actual.get("value") or "")
    if ftype in ("select", "combobox", "radio", "checkbox"):
        return bool(want) and want in got
    return got == want


async def capture_empty_values(session: BrowserSession, fields: list[dict]) -> None:
    """Before anything is filled: record what each custom dropdown shows while empty (its placeholder,
    whatever the widget library), so the submit gate can tell an untouched one from a chosen one."""
    combos = [f for f in fields if f["type"] == "combobox"]
    if combos:
        values = await read_values(session, [f["key"] for f in combos])
        for f in combos:
            f["empty_value"] = values.get(f["key"], {}).get("value", "")


def is_empty(f: dict, actual: dict) -> bool:
    value = actual.get("value") or ""
    if f.get("empty_value") is not None and _norm(value) == _norm(f["empty_value"]):
        return True  # a custom widget still showing its placeholder
    return not (value or actual.get("checked") or actual.get("files"))


def problems_for(ctx: FillContext, values: dict[str, dict], errors: list[dict]) -> list[str]:
    """Human/model-readable list of what's wrong: mismatches, empty required fields, validation errors."""
    problems = []
    for key, f in ctx.fields.items():
        actual = values.get(key, {})
        empty = is_empty(f, actual)
        if key in ctx.written:
            if value_ok(f, ctx.written[key], actual):
                continue  # verified (value_ok knows how widgets show a value, e.g. a swapped-out upload)
            if not f.get("required") and empty:
                continue  # an optional field we couldn't fill is better left empty than failed over
            problems.append(f'[{key}] "{f["label"]}": should be {ctx.written[key]!r}, page shows '
                            f'{(actual.get("value") or actual.get("shown") or "")[:80]!r}')
        elif f.get("required") and empty:
            problems.append(f'[{key}] "{f["label"]}" is required but empty')
    for e in errors:
        label = ctx.fields.get(e["key"] or "", {}).get("label", "")
        problems.append(f'validation error{f" on [{e["key"]}] {label!r}" if e["key"] else ""}: {e["message"]}')
    return problems


class FixOutcome(BaseModel):
    fixed: bool
    unresolved: list[str]


async def fix_with_agent(session: BrowserSession, board: BoardProfile, ctx: FillContext, problems: list[str]) -> None:
    from yahbah.agent.llm import make_crew, run_agent  # llm imports tools, which imports this module lazily

    intended = "\n".join(
        f'[{k}] "{ctx.fields[k]["label"]}" = {v!r}' for k, v in ctx.written.items() if k in ctx.fields)
    task = (load_prompts_config()["prompts"]["agent_fix_task"]
            .replace("{problems}", "\n".join(f"- {p}" for p in problems))
            .replace("{intended}", intended))
    await run_js(session, f"{GUARD_SUBMIT_JS}(true)")
    try:
        await run_agent(with_notes(task, board), make_crew(fill_ctx=ctx), session, max_steps=10, output=FixOutcome)
    finally:
        await run_js(session, f"{GUARD_SUBMIT_JS}(false)")


MapNewFields = Callable[[list[dict]], Awaitable[list[dict]]]
Beat = Callable[[str], None]


async def fill_keys(session: BrowserSession, ctx: FillContext, keys: list[str], beat: Beat) -> None:
    for key in keys:
        value = ctx.value_for(key)
        if value is None or value == "":
            continue
        beat(f"fill [{key}] {ctx.fields[key]['label'][:40]}")
        try:
            await write_field(session, ctx, key, value)
        except FillError as exc:
            logger.warning(f"[fill] [{key}] {ctx.fields[key]['label']!r}: {exc}")
            ctx.written.setdefault(key, value)  # keeps it in the problem list for the fix agent


async def fill_new_fields(session: BrowserSession, ctx: FillContext, map_new_fields: MapNewFields | None,
                          beat: Beat) -> int:
    """Fields that appeared after we started (revealed by earlier answers, or by a first submit):
    map them with the applicant's profile like any other field, then fill them. Returns how many."""
    new = [f for f in await get_form_schema(session) if f["key"] not in ctx.fields and f["label"]]
    if not new or map_new_fields is None:
        return 0
    logger.info(f"[fill] {len(new)} new field(s) appeared: {[f['label'] for f in new]}")
    await capture_empty_values(session, new)
    for f in new:
        ctx.fields[f["key"]] = {"label": f["label"], "type": f["type"], "required": bool(f["required"]),
                                "options": f.get("options") or [], "empty_value": f.get("empty_value")}
    for m in await map_new_fields(new):
        ctx.mappings[m["key"]] = m
    await fill_keys(session, ctx, [f["key"] for f in new], beat)
    return len(new)


async def fill_form(
    session: BrowserSession,
    board: BoardProfile,
    ctx: FillContext,
    map_new_fields: MapNewFields | None = None,
    beat: Beat = lambda _: None,
) -> list[str]:
    """Fill every mapped field; returns the problems still left after retries and the fix agent."""
    await fill_keys(session, ctx, list(ctx.fields), beat)
    for _ in range(2):  # e.g. "If yes, please explain" shows up after answering "Yes"
        if not await fill_new_fields(session, ctx, map_new_fields, beat):
            break

    await close_open_dropdowns(session)
    values = await read_values(session, list(ctx.fields))
    retry = [k for k in ctx.written if k in ctx.fields and not value_ok(ctx.fields[k], ctx.written[k], values.get(k, {}))]
    if retry:
        logger.info(f"[fill] retrying {len(retry)} field(s) whose value didn't stick: {retry}")
        ctx.written = {k: v for k, v in ctx.written.items() if k not in retry}
        await fill_keys(session, ctx, retry, beat)
        await close_open_dropdowns(session)
        values = await read_values(session, list(ctx.fields))

    problems = problems_for(ctx, values, await read_errors(session))
    if problems:
        logger.info(f"[fill] {len(problems)} problem(s) left for the fix agent:\n  " + "\n  ".join(problems))
        beat(f"fix agent: {len(problems)} problem(s)")
        await fix_with_agent(session, board, ctx, problems)
        await close_open_dropdowns(session)
        problems = problems_for(ctx, await read_values(session, list(ctx.fields)), await read_errors(session))
    return problems


def submitted_values(ctx: FillContext, values: dict[str, dict]) -> list[dict]:
    """Per-field record of what we meant to write and what the page shows (artifact)."""
    return [
        {"key": k, "label": f["label"], "type": f["type"], "required": f.get("required", False),
         "source": ctx.mappings.get(k, {}).get("source"), "written": ctx.written.get(k),
         "page_value": (values.get(k) or {}).get("value"),
         "ok": k not in ctx.written or value_ok(f, ctx.written[k], values.get(k, {}))}
        for k, f in ctx.fields.items()
    ]


# ── Fix-agent tools ───────────────────────────────────────────────────────────

def register_fill_tools(tools: Tools, ctx: FillContext) -> None:
    """Key-based fill tools for the fix agent. Deliberately no submit tool."""

    @tools.action(
        "Fill a form field by its key (the 'key' from read_form_schema) with a value. Works for text "
        "fields, dropdowns, radio groups and checkboxes (for these, pass the option text). Read-back verified."
    )
    async def fill_field(key: str, value: str, browser_session: BrowserSession):
        if key not in ctx.fields:
            schema = {f["key"]: f for f in await get_form_schema(browser_session)}
            if key not in schema:
                return ActionResult(error=f"No field with key {key!r}; call read_form_schema for the keys.")
            f = schema[key]
            ctx.fields[key] = {"label": f["label"], "type": f["type"], "required": bool(f["required"]),
                               "options": f.get("options") or []}
        try:
            written = await write_field(browser_session, ctx, key, value)
        except FillError as exc:
            return ActionResult(error=str(exc))
        actual = (await read_values(browser_session, [key]))[key]
        ok = value_ok(ctx.fields[key], written, actual)
        msg = f"[{key}] set to {written!r}; page shows {(actual.get('value') or '')[:80]!r}" + ("" if ok else " (MISMATCH)")
        return ActionResult(extracted_content=msg, long_term_memory=msg)

    @tools.action('Upload a document to a file field by its key. which must be "resume" or "cover_letter".')
    async def upload_document(key: str, which: str, browser_session: BrowserSession):
        path = ctx.files.get(which)
        if not path:
            return ActionResult(error=f'which must be one of {sorted(ctx.files)}')
        try:
            name = await upload_file(browser_session, key, path)
        except FillError as exc:
            return ActionResult(error=str(exc))
        ctx.written[key] = name
        return ActionResult(extracted_content=f"[{key}] uploaded {name}", long_term_memory=f"[{key}] uploaded {name}")

    @tools.action("Read the current value of every form field we know about (key, label, value), plus validation errors.")
    async def read_field_values(browser_session: BrowserSession):
        values = await read_values(browser_session, list(ctx.fields))
        rows = [f'[{k}] {ctx.fields[k]["label"]!r}: {(v.get("value") or "")[:80]!r}' for k, v in values.items()]
        errors = await read_errors(browser_session)
        out = "\n".join(rows) + ("\nValidation errors: " + json.dumps(errors) if errors else "")
        return ActionResult(extracted_content=out[:6000], include_extracted_content_only_once=True,
                            long_term_memory=f"read_field_values: {len(rows)} fields, {len(errors)} errors")
