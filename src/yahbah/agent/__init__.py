"""
Agentic browser layer, ported from the browser-use spike
(~/Documents/browser-use-tests/browser-use, see its CLAUDE_HANDOFF.md).

Principle: deterministic code first, models as fallbacks. Each stage tries
code, then one structured model call, then an agent loop, and checks each
tier's output in code before accepting it — the agent gets the last word.

  stage             1. code                          2. one-shot model                    3. agent loop
  discover          form on page / click Apply       elements -> one element to click     navigation agent
  map form          read_form_schema                 cleaned HTML -> fields + selectors   form-mapping agent
                                                     (selectors verified on the page)     (same verification)
  dropdown options  ARIA-scoped read, keyboard open  — (options only exist once opened)   tiny dropdown agent
  job info          schema.org JSON-LD               metadata model on the page text      —
  answers           autofill tokens, profile/known   constrained mapping call; prose      — (needs_answer)
                    values resolved in code          model for custom questions
  fill              per-type executors + retry       option pick when code can't match    fix agent (no submit)
  submit / verify   gate + page signals in code      verdict call (+ vision if it         fix agent for needs_fix
                                                     contradicts the page)

  llm.py       browser-use model clients per role, Crew, Escalator, run_agent
  tools.py     our named tools (form schema, dropdowns, look_at_screen, gated JS), StallTracker
  boards.py    per-job-board profiles (detection, embeds, stage hooks, model notes)
  discover.py  open → reach the form → map its fields → job info
  fill.py      code-first filling by key, read-back verification, fix agent
  submit.py    gated submit, post-submit verification loop (email codes, fixes)
  session.py   one browser-use BrowserSession per run
"""
import os

# browser-use reads these at import time. Page content and personal data must never leave this machine.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "warning")
os.environ.setdefault("BROWSER_USE_VERSION_CHECK", "false")  # else every Agent start queries PyPI
