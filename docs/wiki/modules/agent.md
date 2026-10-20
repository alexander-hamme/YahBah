# Agent / Browser Automation (`src/yahbah/agent/`)

Replaces the deleted `browser/` package. The module docstring in `agent/__init__.py` has the full tier table; read it first. Uses `browser-use==0.13.11` (pinned in `pyproject.toml`) over local Ollama.

## Stages and tiers
| Stage | File | Tiers (code -> one-shot model -> agent) |
|---|---|---|
| discover (reach form) | `discover.py` | `read_form_schema` / click "Apply" -> element-pick call -> navigation agent |
| map_form | `discover.py` | `read_form_schema` -> cleaned-HTML-to-fields call (selectors verified on page) -> mapping agent |
| job info | `discover.py` | schema.org JSON-LD -> metadata model on page text |
| fill | `fill.py` | per-type executors (text, select, combobox, radio/checkbox, file via CDP), read-back diff + one retry -> fix agent (guarded, no submit; see [Submit boundary](#submit-boundary)) |
| submit/verify | `submit.py` | gate in code, locate, claim, click, then a verify loop decided by structural page evidence (`settings.submit_max_attempts` rounds); the model only advises |

Failures raise `StageFailed` (discover.py) with reasons such as `fill_failed`, `needs_answer`, `submit_button_not_found`, `claim_denied`, `submit_unverified`, `possibly_submitted`, `captcha`, `login_wall`, `job_closed`, `blocked` (the set is `FailureReason` in `run_state.py`). `discover.map_form(force_tier="schema"|"oneshot"|"agent")` can start at a lower tier (used by `scripts/run_pipeline.py --force-tier`).

## Support files
- `controls.py`: the submit/advance control classifier (one JS definition), the submit locator, the agent guard (`GuardedTools`) and the fix-agent tripwire. See [Submit boundary](#submit-boundary).
- `tools.py`: our named browser-use tools (`read_form_schema`, dropdown readers, `look_at_screen`, gated `inspect_page_js` locked until a stall; deny-list is a guard rail, not a sandbox), `StallTracker`, `run_js`. Fields are addressed by an invisible `data-ja-field` key stamped on each element.
- `llm.py`: `chat_model(role)` (a `LoggingChatOllama` that logs raw replies), `Crew` (driver/vision/escalation), `Escalator` (hands steps to the escalation model on stall), `run_agent(task, crew, session, max_steps, output, *, mode="form")` (agent tuned for slow local models; **every** agent phase goes through it, and `make_tools` only builds `GuardedTools`, so every phase is guarded). The step limit is passed by each caller and hard-coded there: navigation 6, dropdown reader 4, mapping agent 15 (`discover.py`), fill-fix agent 10 (`fill.py`). `settings.agent_max_steps` exists but is never read.
- `boards.py`: `BoardProfile` per job board (URL patterns, DOM signature, iframe embed patterns, stage hooks, `model_notes`, `submit_button_js` (a hook returning the control's text or null, tagging it `submit-cta`; no board sets it), and the verification-code email shape: `email_code_subject` as the Gmail subject filter and `email_code_pattern`, a regex the whole code must match, which Greenhouse sets to `[A-Za-z0-9]{8}`; `submit.py` passes both to `GmailClient.wait_for_verification_code`, see [gmail](gmail.md)). Registered: `GREENHOUSE`, `GENERIC` fallback. `detect_board`, `embedded_form_url`, `with_notes`. Workday and Lever URLs are recognised elsewhere (Workday in `credentials.py` alias hashing and `gmail/alert_parser.py`; Lever in `alert_parser.py` and the company-domain heuristic in `workflows/activities/db_ops.py`) but have no `BoardProfile`, so they run as `generic`. The `resolve_application_url` hook (aggregator page -> real application URL) is called by `discover.py` when set, but no profile sets it. `record_observation` appends (board, which tier succeeded at each stage, field count) to `artifacts/_observations.jsonl`, but only from `map_form_activity` after `map_form` succeeds and from `scripts/run_pipeline.py` when its run completes without raising. Failed runs, and runs that never reach `map_form`, leave no observation. (The `boards.py` module docstring says "every run"; that is inaccurate.)
- `session.py`: `SessionRegistry` (singleton, `run_id` -> `BrowserSession`, own temp Chrome profile each) and `artifact_path(run_id, filename)`.

## Conventions
- Board-specific knowledge goes in a `BoardProfile`, not in generic code; fixtures guard against cross-board regressions ([development](../development.md)).
- Models only get roles from `yahbah/llm/models.py` ([llm](llm.md)); prompts are in `config/prompts.yaml` (`oneshot_*`, `agent_*_task`, `option_pick`, `post_submit_judge`).
- Activities wrapping these stages: `src/yahbah/workflows/activities/browser.py` ([workflows](workflows.md)).

## Submit boundary
Roadmap A3/A4, contract [run-lifecycle](../contracts/run-lifecycle.md) §4/§6/§8. Principle: **code evidence decides, models advise; agents can never submit.**

### Control classifier (`controls.py`)
One JS definition decides what a click (or key) on an element would do: `submit`, `advance` (Next, Continue, Save, Review, Skip) or `other`.
- It resolves the control first: the nearest button-like ancestor (`button`, `a`, `[role=button|link]`, `[onclick]`, `input[type=submit|button|image]`). An option, radio, checkbox, combobox, select or plain input is never a button, whatever it says; a `<label>` is the control it activates.
- A **native submit** (`type=submit`, or a `<button>` with no type inside a form) is never `other`: `advance` when its label reads like a step button, else `submit`. A label alone decides only for controls with no structural signal (`type=button`, `role=button`, links, `onclick` divs). Labels are word matches ("Next month" is not an advance).
- `FIND_SUBMIT_JS` (the pipeline's own locator) uses the same classifier: among the `submit` controls in the form holding our fields, a native one whose whole label reads like submitting wins, else the only one. A step button is **never** the final submit: with only `Next`/`Continue` on the page the stage fails `submit_button_not_found` before anything is recorded. A disabled control is waited for (3 s), then `submit_button_not_found`.

### Agent guard and tripwire
- `GuardedTools(Tools).act()` runs before every agent action and answers a refusal as an error result. It refuses: a click on a `submit`/`advance` control; Enter (implicit form submission; allowed only in a textarea or an open dropdown, never with ctrl/cmd/alt) or Space on a focused submit/advance control; typing (`input`) into anything but a text control (a button, link, checkbox or select is a key press away from activating), in every mode, and a newline typed into a non-textarea; a coordinate click. The focused element is found through shadow roots and same-origin frames; focus in a frame that can't be inspected refuses Enter and Space (fail closed). In form modes it also refuses `navigate`, `search`, `go_back` and `close`.
- `run_agent(mode=...)` sets how strict it is: `navigate` (discover's navigation agent: clicking "Apply" on a job page must work, so controls are off limits only once the page looks like an application form, `discover.looks_like_form`, or the control sits in a `<form>` of its own with two or more fillable fields), `form` (default; mapping and dropdown agents: always off limits), `fix` (the fill-fix agent: as `form`, plus native `submit` events are cancelled at window level while it runs, and afterwards the **tripwire**).
- Approved answers are locked: with no mapper (`map_new_fields=None`, the review re-fill and the submit step) `FillContext.locked` is set. The fix agent's `fill_field` then writes exactly the approved answer (or an option that means it) and refuses anything else, `upload_document` only the approved document, and `problems_for` reports a field whose last write was not the approved value, so neither the fill nor the submit gate reports clean for a form that differs from what the user approved. A field with *no* approved answer must also keep what it showed once code finished filling (`FillContext.baseline`, taken by `fill_form`): an agent typing into it, ticking it or choosing an option there is a problem at the fill and at the gate. The baseline is taken only by `fill_form` (after code's fill, before any agent) and carried to the submit step as data: `FillContext.baseline`, a `dict[str, str]` (field key -> JSON string `[text, ticked, file count, shown text]`) for the fields without an approved answer. `submit_and_verify` in locked mode without one fails closed (`fill_failed`, "no approved-state baseline") before the gate and the claim, because a baseline taken there would read agent text as a starting value. With it, the gate and every post-claim fix round check against it. The `input` guard does not additionally refuse such typing; the state check catches it whatever action did it.
- Tripwire (`fix` only): if the document was replaced (a stamp on `window` is gone), the URL changed (fragment ignored) or the form's fields vanished, `StageFailed("possibly_submitted")`, also when the agent run ends in a cancellation (the activity must record the possible submission, not end the run CANCELLED). If a link opened another tab, focus is returned to the form's tab and that alone does not trip it. The activity records the possible submission and ends the run `SUBMIT_UNCERTAIN`.
- Limits (known): an unclassified control that submits by XHR without changing the DOM within the agent run is not seen; the mapping and dropdown agents have no tripwire (they run before anything is filled); the navigation agent may click a submit-looking control that sits outside any form on a page with fewer than three fields (one-click "Apply" sites are indistinguishable from navigation).

### `submit_and_verify` (`submit.py`)
```
submit_and_verify(session, board, ctx, email, already_attempted, claim, map_new_fields=None, beat=..., record=None) -> SubmitResult
```
1. **Gate** (nothing recorded): new fields are mapped and filled if there is a mapper, else a *required* one without an answer is `needs_answer`; empty/mismatched required fields or validation errors are `fill_failed`.
2. **Locate** the submit control (`submit_button_not_found`; nothing claimed).
3. **Claim**: `await claim()` once, right before the first click; `False` -> `claim_denied`, no click. Not called when `already_attempted`.
4. **Click**, then up to `settings.submit_max_attempts` **rounds**. A round waits until the page shows something decisive on two polls in a row, or `ROUND_SECONDS` (4 s) pass (a spinner, `aria-busy` or a disabled submit control extends that by up to 20 s), then judges the structural evidence (`assess`):

| verdict | meaning | action |
|---|---|---|
| `submitted` | our form's fields (fewer than half left) and the submit control are gone, no error text, and the visible text carries a confirmation phrase | success, no model call |
| `probable` | same, no confirmation phrase | success only if the model's advice is `submitted` (`decided_by: "code+model"`) |
| `errors` | the form is still there and validation errors were found in the DOM | fix (new fields, rewrite the fields the errors name, fix agent), re-gate, click again |
| `code_prompt` | verification-code inputs | fetch the code, enter it (read back), click again |
| `pending` | nothing decisive: a no-op click, "thank you" text with the form still there, an in-flight request | wait another round; **never click** |

   After the last round's re-click there is one more observation (`kind: "final"`). Still unproven: `StageFailed("submit_unverified")`; never a flagged result.
5. **Model advice** (`PostSubmitState`, driver, then vision if it fails or is inconsistent): asked only for `probable` and for a last-round `pending`. It can complete `probable` or name a blocker (`captcha`, `login_wall`, `job_closed`, `blocked`, used as the failure reason); a `submitted` it gives for a `pending` page counts for nothing. Its `problems` are recorded as quoted data and never reach an agent.
6. **Verification codes**: `GmailClient.wait_for_verification_code(email, since_epoch=<click>, subject_hint=board.email_code_subject, code_pattern=board.email_code_pattern, exclude_message_ids=<tried>)`. `finalize_code_message(id)` runs only once the page has moved on from the code inputs; a code the page keeps asking for again is *rejected*, never finalized, and the next message is fetched. No email in time: `email_code_timeout`; inputs that don't show the typed code: `email_code_entry`.

Everything after a `True` claim is `StageFailed` unless verified (`submit_unverified`, `fill_failed`, blocker reasons, `email_code_*`, `possibly_submitted`); unexpected exceptions (a click that raised, a lost CDP connection) are converted to `submit_unverified`. The caller ends such a run `SUBMIT_UNCERTAIN` and never clicks submit for it again. A server-side validation round trip that reloads the page loses the field keys; that is `submit_unverified` ("fields can no longer be addressed").

### Submit evidence record (`record`)
`record(dict)` is awaited once per observation: every verify round (`kind: "round"`), plus the closing pass after a last-round re-click (`kind: "final"`). A record that raises is logged and ignored. The same dict is `SubmitResult.evidence` for the closing observation. All values are JSON-serializable; page-derived strings (`signals.excerpt`, `signals.errors[].message`, `judge.problems`) are **untrusted data**: render as text only.
```json
{"schema": 1, "kind": "round|final", "round": 1, "at": "ISO-8601 UTC", "elapsed_s": 4.6,
 "clicks": 1, "attempt": "first|verify_only", "url_before": "…", "url": "…",
 "verdict": "submitted|probable|errors|code_prompt|pending",
 "decided_by": "code|code+model",
 "action": "accepted|waiting|fix_and_reclick|enter_code_and_reclick|stop",
 "signals": {"doc_replaced": false, "form_gone": true, "controls": 0, "controls_before": 13,
             "submit_control": {"present": false, "visible": false, "disabled": false},
             "errors": [{"key": "3", "message": "…"}], "code_inputs": 0, "in_flight": false,
             "confirmation_markers": ["thank you for applying"], "error_markers": [], "excerpt": "first 300 chars"},
 "judge": null,
 "code": null}
```
`judge`, when asked: `{state, blocked_reason, vision, problems[≤10, ≤200 chars], reasoning[≤300 chars]}`. `code`, after a code was entered: `{message_id, outcome: "accepted|rejected"}` (the code itself is never recorded). `clicks` counts submit clicks of this attempt.

### Fill evidence and new fields (`fill.py`)
- `submitted_values(ctx, values)` -> the contract §8 rows `{key,label,type,required,options,answer,source,basis,generated,written,page_value,ok}`. `answer` is the mapping's intended value (a document is its file name, never its path); `source`, `basis`, `generated` come from the mapping dict unchanged.
- `fill_form(..., map_new_fields=None)` leaves fields that appear while filling alone and records the **required** ones without a mapping on `ctx.unmapped_new_fields` (schema-row shape, dropdown options read); they are not `problems`. The caller (the activity) returns them and sends the run back to review. With a mapper they are mapped and filled as before. `submit_and_verify` runs the same check before the claim and, with no mapper, refuses (`needs_answer`).
- `match_option` takes prefixes as whole words ("No" does not match "Now").
