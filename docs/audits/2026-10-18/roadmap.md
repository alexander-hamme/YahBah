# Engineering Roadmap: 2026-10-18 Specialist Audit Synthesis

- **Date:** audits 2026-10-18; synthesis 2026-10-19
- **Author:** main session (Engineering Lead), from the seven specialist reports below
- **Revision examined:** branch `browser-use` @ `2911263`. `89b1667` was committed mid-audit and touches only `CLAUDE.md`, so the source code is identical.
- **Status:** Point-in-time plan, not authoritative architecture. Check findings against current source before acting on them.

| Report | Agent | Findings |
|---|---|---|
| [browser-navigation.md](browser-navigation.md) | browser-navigation-engineer | 5 (4 confirmed) |
| [application-forms.md](application-forms.md) | application-forms-engineer | 5 (4 confirmed) |
| [llm-intelligence.md](llm-intelligence.md) | llm-intelligence-engineer | 5 (4 confirmed) |
| [gmail-job-discovery.md](gmail-job-discovery.md) | gmail-job-discovery-engineer | 5 (all confirmed) |
| [temporal-platform.md](temporal-platform.md) | temporal-platform-engineer | 5 (all confirmed) |
| [product-dashboard.md](product-dashboard.md) | product-dashboard-engineer | 5 (4 confirmed) |
| [qa-evaluation.md](qa-evaluation.md) | browser-agent-qa-engineer | 5 (4 confirmed) + failure taxonomy |

Probe scripts and synthetic pages cited in the reports are kept in [probes/](probes/). Personal config and the real third-party page capture were left out. The main session re-checked the claims that drive priority against source: retry/dedup, the alert-poller startup cycle, `contradicts()`, the submit guard, the answer-evidence check and cache precedence, `match_option`, `gh_jid` stripping and the raw HTML sink.

## Where YahBah stands

1. **The design is sound.** All seven specialists endorsed the core decisions:
   - code-first tiers, with every tier verified in code;
   - submission only through gated code;
   - named agent tools (no `evaluate`);
   - local models with fixed options (no reloads);
   - a clean migration chain.

   Nobody recommends a rewrite.
2. **The new pipeline has never run for real.** None of the 338 runs in the DB comes from the revamped pipeline. They all date from 2026-03 to 2026-06.
   - The revamp's only field evidence is six `run_pipeline.py` debug runs on one Greenhouse URL, with zero submissions.
   - `ApplicationWorkflow` first executed against Temporal in the audit probes.
   - Function coverage is 14%. The discover ladder, `submit_and_verify`, the workflow, activities, Gmail and URL code have no tests.
3. **Discovery has produced no usable jobs.** All 302 scheduled jobs were jobright.ai aggregator pages. All 301 promoted runs failed at discovery, in one 25-second burst. 62% of them were already closed at ingestion. URL normalization strips the real Greenhouse job id (`gh_jid`) and keeps tracker ids.
4. **The safety invariants fail at the boundaries.** The weak points sit between model verdicts and code, agents and the submit button, the workflow and the DB, and one run and the next. Those failures came out as reproductions, not theory:
   - The real judge model returned "submitted" 4/4 times on an unsubmitted form showing one line of "thank you" text.
   - A worker restart resurrected a run marked FAILED, which then submitted (Temporal probe B).
   - A live run cached a fabricated answer.
   - The submit guard let 4 of 7 SPA submit-control shapes through.

**Implication:** the next milestone is not a feature. It is a first live submission that is safe and observable, then discovery that yields real jobs, then coverage of unfamiliar portals (Workday).

## Guiding direction (user, 2026-10-19)

The revamp replaces the static paradigm (the form is on the page, it has the expected fields, there is one submit) with **step-aware, multi-approach page-interaction loops** that take any job URL. The loops must be able to:
- click through to a form that isn't there yet;
- get past, or report, a login or account wall;
- walk multi-step flows;
- answer every field appropriately, including open-ended curveballs ("What kind of fruit are you?", "Tell us about a time you built something end to end").

Every interaction climbs the same tier ladder, and each tier is verified in code:
1. deterministic code, seeded by board profiles (`boards.py`);
2. one structured model call, with the board template as context where useful;
3. an agent loop with vision and browser-use actions.

**Replace, don't layer:** static-paradigm code is removed when its replacement lands. No parallel old and new paths, no dead code kept for compatibility.

Several findings are the old paradigm surviving inside the new code:

| Static assumption still in code | Where | Finding |
|---|---|---|
| A fixed pipeline: map the form once, map answers once, fill once, submit once | `workflows/application.py` stages | Forms F3 |
| "Is this the form?" decided once, by counting fields | `looks_like_form`, discover tier 1 | Nav F1 |
| The form lives in the top document | `run_js`, `READ_FORM_SCHEMA_JS` | Nav F3, Forms F4 |
| Fixed sleeps instead of waiting for the page to change | `session.py`, `settle(3)`, the 3 s submit wait | Nav F5, Forms F1 |
| Submit is one terminal click, and an LLM names the result | `agent/submit.py` | Forms F1, QA F3 |
| A login or account page is a terminal failure | no account capability | Nav F1 |
| The one-shot tier sees only the viewport | `llm_representation()` input | Nav F5 |

The batch-1 safety work is therefore not a detour. Agentic loops on unfamiliar pages are exactly where a stray submit or a false "submitted" happens, so the loops need these safety measures first. Batch 2's centrepiece is the loop itself (B0).

## Shared root causes

| # | Root cause | Reported by | Consequence |
|---|---|---|---|
| R1 | "Possibly submitted" is not a state. Uncertain becomes FAILED, a test run becomes COMPLETED, and dedup checks only `status == COMPLETED`. | Forms F2, Temporal F2, Product F1, QA F2, Gmail F3 | Retry, a repeated alert or a manual add re-applies to the same job. Test runs block real applications. Uncertain runs are hidden in the UI. |
| R2 | No owner fences run state or the browser. Workflow, worker, API cancel and API retry all write `status`. | Temporal F1 (probes B, C) | Restart resurrects a FAILED run, two executors run on one run, and `max_concurrent_runs` is not enforced. |
| R3 | Model verdicts are accepted where code evidence should decide. | Forms F1, QA F3, LLM F1, QA F4; Nav F1 has the same pattern with a weak code heuristic | False COMPLETED, unwarranted re-clicks, fabricated applicant facts, the wrong form accepted. |
| R4 | The agent/submit boundary is enforced only in page JavaScript, only for native form submits, and only around one of four agents. | Forms F5, QA F1, Nav (cross-note) | An agent click can submit outside the gate, with no record. |
| R5 | Untrusted content reaches trusted sinks. | Product F2, QA F3/F4, LLM F1/F3, Gmail F1 | Raw job-site HTML in the dashboard, job-description text accepted as applicant evidence, page text deciding success, unvalidated URLs from email, an unauthenticated API reachable on the LAN. |
| R6 | Discovery admits jobs without resolving or gating them. | Gmail F1–F3, Nav F2, Temporal F3, LLM F3 | No usable jobs, unreviewed live submissions, a score computed on aggregator text. |
| R7 | Failures leave no evidence. | QA taxonomy, Nav F4, Product F3, Temporal F5, LLM F5, Gmail F4 | Systemic failures go unnoticed (the jobright burst), cannot be reproduced, and nothing measures success rates. |

## Conflicting recommendations: decisions

1. **Worker-restart policy → resume** (Temporal F1). The activities already re-open the browser and check that the form matches. The one unsafe point, losing the browser after the click, is already non-retryable and becomes `SUBMIT_UNCERTAIN`. Remove the RUNNING→FAILED flip in `fail_incomplete_runs` and add heartbeat timeouts. Cancel remains the way to stop a run.
2. **Shape of the uncertain outcome → one source, three views.**
   - A new terminal status, `SUBMIT_UNCERTAIN`.
   - A structured `failure_reason` column.
   - A server-derived `outcome` field for the UI.
   - One Python predicate, `possibly_applied()`, called by every dedup site.

   The frontend holds no status logic. This combines the Temporal, Forms and Product proposals.
3. **Retry semantics → a new linked run.** Retry creates a new run (`retry_of_run_id`). The original row, its evidence and its email alias stay untouched. Retry returns 409 when the original has `submit_attempted_at`, unless the user explicitly confirms nothing was submitted. Retry and cancel stop the old workflow first. This replaces both the current reset-in-place behaviour and Product's attempt-counter idea.
4. **Agent submit guard → action-level, not network-level.**
   - One submit/advance button classifier, shared by `click_submit` and a wrapper around the agent's click tool.
   - A tripwire after each agent step: URL changed or form gone means "possibly submitted".

   **Rejected:** blocking non-GET requests during agent phases (a QA option). Legitimate fills send POSTs: résumé uploads, autosave, typeahead lookups.
5. **Admission → stopgap now, dispatcher later.** Set `max_concurrent_activities` explicitly and cap promotions per cycle. Temporal's PENDING-queue dispatcher is the right eventual design, and the natural home for ranking and max-K per company. Build it once discovery actually yields jobs.
6. **Ownership of the form-classification code → split by layer, in sequence.** Forms owns the button classifier and the agent tool wrapper in batch 1. Navigation's page classifier (application form / account wall / search) follows in batch 2 and exposes the container that the submit locator is scoped to. The two never edit `tools.py` at the same time.
7. **Verify-only mode can still click** (Forms, Temporal and Product all flagged it) → closed by decision 3 plus A3. A retried run no longer inherits `submit_attempted_at`, and within one workflow a re-click requires code evidence.
8. **Submit policy → a review gate for every run** (the user's decision, which supersedes Gmail F1's "fill-only for discovered jobs"). See A7.

## A. Must-fix

| ID | Item | Sources | Effort |
|---|---|---|---|
| A1 | **Discovery cannot launch unreviewed submissions.** Startup cycles respect the enabled flags; unscored jobs are never admitted; a promotion cap per cycle and expiry by age; an http(s)/public-host URL policy at intake. Every run follows the review gate (A7). | Gmail F1, Temporal F3 | S–M |
| A2 | **Submission state, run ownership and duplicate protection.** `SUBMIT_UNCERTAIN`, `failure_reason`, the `possibly_applied()` predicate at every dedup site, an atomic submission claim, fenced status writes, a `SessionRegistry` owner token, resume on restart with heartbeat timeouts, retry and cancel per decision 3, test runs excluded from "applied". | Temporal F1/F2, Forms F2, Product F1, QA F2 | L |
| A3 | **Post-submit verification needs code evidence.** A `SubmissionEvidence` record decides "submitted"; a pending state replaces the fixed sleep; re-clicks only on code-detected errors or code inputs; LLM `problems` passed as quoted data; one more evidence pass after the last click; per-round evidence saved. | Forms F1, QA F3 | M |
| A4 | **Agents cannot activate submit or advance controls.** A shared classifier, a click/Enter wrapper in every agent phase that runs on a form page, and a post-agent tripwire; mark the attempt only after the button is located. | Forms F5/F2.5, QA F1 | M |
| A5 | **Facts are retrieved, narratives are generated, and nothing generated asserts a new fact.** Factual screening questions (authorization and sponsorship per jurisdiction, clearance, years per skill, start date, salary, EEO) are answered only from typed stored facts; otherwise `NeedsAnswer`, which surfaces in review. Open-ended and personality questions ("what kind of fruit are you?", behavioural STAR questions) are always answered, grounded in the profile's experience bullets with no invented employers, projects or metrics, and labelled `generated` for review. Answers cite stored keys, not quotes; the job description never counts as evidence. The cache records provenance, comes after stored answers, never caches conditional or company-specific labels, and keeps debug runs in their own namespace. Today's quote check can also fail the other way: a required curveball with nothing quotable may end the run as `NeedsAnswer`. | LLM F1/F2/F4, QA F4 | M–L |
| A6 | **Untrusted HTML and LAN exposure.** Render confirmation text, or a sandboxed iframe, instead of raw HTML; `next dev -H 127.0.0.1`; `TrustedHostMiddleware`. | Product F2 | S |
| A7 | **Review gate (user decision).** A global `submission_mode` replaces the boolean `testing_mode` and is recorded on each run at creation.<br>• **`review` (default):** fill the form; save the evidence (each answer with its basis and the value read back from the page, plus a screenshot of the filled form); close the browser; wait as `AWAITING_REVIEW` for an approve signal for up to `review_window_hours` (default 48). On approval, re-open the form, re-fill exactly the approved answers and submit. A field that was not approved sends the run back to review. With no approval the run ends `EXPIRED`: nothing is sent, and it can be revived.<br>• **`auto_reviewed`:** after filling, the prose model (`qwen3.8:27b`) reviews every answer in a fresh context against the stored facts and the filled-form screenshot. All clear means submit immediately; any flag sends the run to the review queue.<br>• **`testing`:** fill only. | User, Gmail F1, Product F1/F5 | M (review), M (reviewer) |

**Small fixes that ride along:**
- keep `gh_jid` and strip `imp_id` (Gmail F2);
- the verification-code heuristic, plus a generic subject filter, which fixes a revamp regression (Gmail F5);
- whole-word `match_option` prefixes, so "No" no longer matches "Now" (LLM F1);
- `temporal server start-dev --db-filename` (Temporal F4);
- `ON DELETE SET NULL` for `scheduled_job.promoted_run_id`, which today blocks deleting 301 runs (Product F4).

## B. High-value improvements

| ID | Item | Sources | Notes |
|---|---|---|---|
| B0 | **Step-aware application loop (centrepiece of batch 2).** A page-state machine in code: observe → classify the page (job page, apply CTA, account wall, form step, review step, confirmation, closed, error) → act through the tier ladder → verify that the page advanced → repeat. Fields are mapped per observed step instead of once, and the review gate pauses at the final submit. It replaces the static MAP_FORM / MAP_ANSWERS / FILL stages and absorbs B1, B5's wizard loop, B8 and account-wall detection. | Forms F3, Nav F1/F5, guiding direction | Spec-first; the main session owns the loop contract. Nav (page states, navigation) + Forms (step fill, advance, submit), with LLM (per-step mapping), Temporal (activity shape: one heartbeating loop activity or one activity per step) and QA (multi-step fixture corpus). |
| B1 | Form-ready page classifier, plus code-side account-wall detection; submit locator scoped to the classified container | Nav F1 | Needed before Workday; removes false form-ready on search bars, résumé widgets and Create Account pages |
| B2 | Job identity and aggregator resolution: per-board normalizer with an `(ats, tenant, job_id)` key; links resolved before they are filtered; a `JOBRIGHT` profile with resolver and code-side closed detection; the resolver called at admission | Nav F2, Gmail F2 | Needs 2–3 jobright fixtures saved by the user. Then re-measure per-sender yield, since direct ATS links are currently dropped by the regex. |
| B3 | Scheduled-queue state machine: atomic, idempotent promotion independent of Gmail; APPROVED promotes; REJECTED sticks; admission data carried into the run | Gmail F3, Temporal F3 | Foundation for max-K and the archive view |
| B4 | Match score: requirements extracted once, scored in code, stored per posting, reused at promotion, calibrated on about 50 labels from the user | LLM F3 | Depends on B2 (employer job description, not aggregator text) |
| B5 | Unfamiliar-portal coverage: generic iframe-embed following; ARIA widgets, field constraints and a check that every required control is covered; a step-aware wizard loop | Nav F3, Forms F3/F4 | Prerequisite for Workday |
| B6 | One canonical profile and serializer; `fallback_answer` prompt fixed (drop the NDA and over-claiming instructions, include experience bullets); cover letter only when the form asks for one | LLM F4 | Prerequisite for profile editing in the UI |
| B7 | Gmail durability: a durable inbox table, pagination, single-flight locks, pre-filtering before LLM calls, poller health; status emails attributed by alias only | Gmail F4/F5 | Needed before polling is turned on for real |
| B8 | Discover efficiency: one-shot candidates taken from the whole page, condition-based waits | Nav F5 | Cuts calls to local models that take 7–150 s each |
| B9 | Supervision UX: a "Needs you" queue for `needs_answer`; promoted-run outcome shown on the scheduled page; skipped jobs approvable; typed `RuntimeSettings` that fail closed | Product F5 | Overlaps the planned settings and profile features |

## C. Foundational work

| ID | Item | Sources |
|---|---|---|
| C1 | **Shared test harness.** A local HTTP fixture server (browser-use does not index `about:blank`), scripted LLM and judge stubs, mock-ATS behaviours, a Temporal `WorkflowEnvironment` with stub activities, and a disposable Postgres test DB. Seeded from [probes/](probes/). | QA F5, Forms plan 1, Temporal F5, Nav F4 |
| C2 | **Structured outcomes and an event log.** A `failure_reason` column; `application_step` written for each stage attempt and never deleted; an observation on every exit, not only successes; a sanitized DOM snapshot on failure. | QA taxonomy, Nav F4, Temporal F5, Product F3 |
| C3 | **UI catches up with the backend.** The `outcome` field, the new `submitted_values` shape, the new screenshot labels, a trace endpoint and timeline, error messages for failed requests, ESLint. | Product F3/F4 |
| C4 | **LLM evals and call tagging.** About 40 screening-fact items on the example profile, a judge eval, a `task` and prompt hash on every logged call, temperature 0 with a seed, logs for Gmail-side calls. | LLM F5, QA plan 4 |
| C5 | Postgres↔Temporal reconciler | Temporal F4 |
| C6 | **Generalization corpus and replay benchmark.** 15–20 saved pages across boards, plus minimal CI running `pytest -m "not live_llm"`. | QA plan 3 |
| C7 | **Acceptance gate:** one end-to-end Temporal run in testing mode on an offline fixture, before any unattended live submission | Temporal F5 |

## D. Future opportunities (deferred)

- **Workday and accounts:** a Workday `BoardProfile`, and agentic account creation (needs a product/consent decision; `generate_password` is never called). After B1 and B5.
- **Admission dispatcher** with ranking and max-K per company. After B2–B4.
- **Deeper DOM reach:** shadow-DOM deep queries, a frame-aware `run_js`, cross-origin iframes beyond known embeds, vision-based filling. Revisit after B5 shows what is left.
- **More discovery sources:** Gmail push, querying job boards directly, parsing recruiter emails. LLM-based dedup and natural-language blacklists need B2's identity keys first.
- **Multiple users or profiles.** Needs a profile id on runs (after B6). The settings sidebar comes after B9's typed settings.
- **Workflow versioning and `Replayer`** once real histories exist. Now, with no executions in flight, is the cheapest time to reshape the workflow.
- **Before any LAN or remote use:** a local API token. Later still: OpenAPI-to-TypeScript codegen (needs a dev dependency).
- **Polish and privacy:** Framer Motion and visual polish; local company logos instead of Google favicon fetches (privacy and icon lag).
- **Not needed at this scale:** multiple workers, OpenTelemetry, embeddings for answer reuse, fine-tuning, escalation-latency tuning.
- **Hygiene:** drop the unused `anthropic` and `pydantic-ai` dependencies with the next dependency change.
- **Next.js patch upgrade** (found in wave 1): `npm audit` reports 2 critical advisories that predate the batch, `next` 16.2.1 (Server Components DoS) and `proxy-addr`. Exposure is low for a loopback-only app; do it as its own small task.
- **browser-use default extensions** (found in wave 1, browser-navigation): they are downloaded from the Chrome Web Store and change pages, but they also dismiss cookie banners. Decide keep, vendor or disable as part of B0/B1, with measurement. The PyPI version check is already disabled.

**Not adopted:**
- Network-level request blocking (decision 4).
- The admission redesign now (decision 5).
- Automatically resuming uncertain runs. They need a human decision by design.

## First batch (proposed, pending user review)

**Goal:** the first live submission is safe, recorded and provable. **Gate:** no unattended live submission until tasks 1–5 land and C7 passes.

**Step 0, main session, before wave 2 starts: shared contract.** A short decision record in the wiki fixing:
- the run status values (adds `AWAITING_REVIEW`, `EXPIRED`, `SUBMIT_UNCERTAIN`) and the `failure_reason` vocabulary;
- the table that derives `outcome`;
- the semantics of `possibly_applied()`;
- `mark_attempted` → `claim_submission() -> bool`, an atomic claim that the in-browser code checks before clicking;
- `submission_mode` (`testing | review | auto_reviewed`) and `origin` (`manual | discovered`) on the run and the workflow input;
- the approve/reject signals, and the shape of the review evidence: each answer with its basis and the value read back from the page, plus the filled-form screenshot;
- `retry_of_run_id`;
- migration ids: `0018` for task 3, `0019` for task 5;
- file ownership per task.

| # | Task | Lead / support | Wave | Outcome & acceptance |
|---|---|---|---|---|
| 1 | **Invariant harness and regression suite** (C1) | QA / — | 1 | **Outcome:** the shared harness exists, and every confirmed A2–A5 defect has a test marked `xfail(strict=True)`; the owners flip them. **Acceptance:** runs offline on localhost only, in under 60 s; `uv run pytest` is green apart from those xfails. No `src/` changes. |
| 2 | **Discovery gate** (A1 + the `gh_jid`/`imp_id` and code-heuristic fixes) | Gmail / Temporal (review) | 1 | **Outcome:** nothing that arrives by email gets past the review gate or skips admission. **Acceptance:** a fake `GmailClient` with polling disabled triggers no label change and no run; a scoring failure is never promotable; loopback, RFC 1918 and `file:` URLs are rejected; two `gh_jid` jobs stay distinct; "Thanks" is not accepted as a code. |
| 3 | **Submission state, run ownership and the review gate** (A2 + A7 review mode) | Temporal / Forms (claim call site), Product (UI) | 2 | **Outcome:** no path produces two submit clicks for one job; uncertain runs are visible and never re-applied silently; and nothing is submitted without approval in `review` mode. **Acceptance:** `WorkflowEnvironment` tests cover:<br>• a restart mid-stage (the run is never shown FAILED, and peak RUNNING never exceeds N);<br>• a retry while the old workflow is alive;<br>• timeout overlap;<br>• every dedup site seeing an attempted run;<br>• review: waits for approval, expires with nothing sent, and an approval re-fills and submits exactly once.<br>Retry of an attempted run returns 409. A COMPLETED test run does not block a real one. |
| 4 | **Submit boundary** (A3 + A4) | Forms / QA (mock-ATS cases), Nav (review of agent phases) | 2 | **Outcome:** structural evidence decides success and re-clicks, and agents cannot submit. **Acceptance on mock-ATS fixtures:** slow success gives exactly 1 click and COMPLETED; a no-op click, or misleading "thank you" text, never ends COMPLETED; `needs_fix` without code-detected errors does not re-click; the 7 SPA submit shapes are all blocked in every agent phase; no marker is written when no submit button is found. |
| 5 | **Answer integrity and quality** (A5 + B6's prompt and profile fixes) | LLM / Forms (`match_option`) | 2 | **Outcome:** no material fact reaches a form unless a stored key or the user's approval backs it, and every open-ended or curveball question gets a grounded answer. **Acceptance:** the audit repro tests flip. The eval (opt-in, live models) shows 0 critical-fact errors, at least 95% `NeedsAnswer` recall on factual screening questions, and 100% answered on curveball and behavioural items with no invented facts. Editing a stored answer changes the next answer without a worker restart. |
| 6 | **Dashboard security and review queue** (A6 + A7 UI) | Product / — | 1 → 2 | **Outcome:** job-site content cannot run script in the dashboard, the LAN cannot reach it, and the user can review a filled application and approve it. **Acceptance:** a component test shows `<img onerror>` does not execute; the dev server does not answer on the LAN IP; the review view shows each answer with its basis, the value read back from the page and the screenshot, plus approve/reject and a three-way mode toggle that names the current mode. |

**Execution:**
- **Wave 1:** tasks 1, 2 and 6's security part run in parallel.
- **Wave 2:** tasks 3, 4 and 5, plus 6's review UI, run in parallel in isolated worktrees, against the Step 0 contract and the task-1 harness.
- **Integration order:** 3 → 4 → 5, because of the migration chain and the claim interface.
- **Close-out:**
  1. QA independently reviews tasks 3 and 4.
  2. The C7 end-to-end run.
  3. One wiki pass by `project-knowledge-maintainer`. Every audit flagged stale "In flux / uncommitted" text, and the restart, observation-log and status-poller claims are wrong.

**File ownership** (wave-2 overlaps are resolved by interface, not shared edits):
- Task 3 owns `workflows/`, `worker.py`, `db/`, `alembic/0018`, `db_ops.py`, `activities/browser.py`, `api/routes/runs.py`, `jobs.py`, `agent/session.py`, and the dedup sites in `alert_poller.py` and `scheduled.py`.
- Task 4 owns `agent/submit.py`, `agent/fill.py` and `agent/tools.py`.
- Task 5 owns `llm/`, `config/prompts.yaml`, `config/personal.example.yaml` and `alembic/0019`.
- Task 2's small workflow-input change lands in wave 1, before task 3 starts.

**Batch 2 candidates:**
- **B0, the step-aware loop:** written spec-first, with Nav and Forms co-leading. It absorbs B1, B5's wizard loop and B8.
- **The `auto_reviewed` reviewer pass** (LLM). Needs task 5's basis output, plus a reviewer eval: its catch rate on seeded wrong answers.
- **B2:** once the jobright fixtures exist.
- **B3:** includes deciding whether the scheduled queue's promotion delay is still needed now that every run waits for review after filling. Then B4.
- **C2 with C3, and C5.**
- **Then B5's iframes and widgets**, before any Workday work.

## Decisions (resolved 2026-10-19)

1. **Submission policy:** every run passes the review gate (A7). Unapproved runs expire after the window; nothing is auto-submitted on timeout. `auto_reviewed` is opt-in, and the user can toggle it.
2. **Test database:** approved in principle; the command is pending the user's go-ahead.
3. **Defaults stated by the main session, not objected to:** resume runs after a worker restart (decision 1); retry creates a new linked run (decision 3).
4. **Overhauls are welcome:** remove superseded static-paradigm code rather than keep it alongside the new code.

## Operational notes (as of 2026-10-19)

- **Do not start the worker until task 2 lands.** Its startup cycle ignores `job_alert_polling_enabled: false`. It would ingest and archive about 4 months of alerts and admit every job (`job_alert_min_match_score: 0`). Promotion at the next start after the 24 h delay would then launch live runs (`testing_mode: false`). Interim mitigation: `testing_mode: true` and empty `job_alert_sender_patterns`.
- The API process on :8000, started 2026-10-16, returns 500 on every DB-backed GET while the same code in-process returns 200. Restarting it is likely to fix this.
- The running Temporal dev server is in-memory and holds no workflows. Restarting it with `--db-filename` loses nothing.
- `CLAUDE.md` refers to a `project-wiki-manager` agent; the configured agent is `project-knowledge-maintainer`.
