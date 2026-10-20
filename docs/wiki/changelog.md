# Changelog

Running record of architectural, structural, decision-making and workflow-paradigm changes, newest first. Not a commit log: no function- or bug-level detail (`git log` has that).

- **Practice:** every architecturally meaningful change (structure, interfaces, decisions, workflow paradigm) adds an entry when it lands; each batch/wave close-out adds one. Keep entries terse. Maintained by `project-knowledge-maintainer`, reviewed by the main session.
- **Entry shape:** What / Why / Governs (commit, report or contract) / Supersedes. Short SHAs are as of 2026-10-19; `browser-use` history has been rewritten before (the reflog shows amends; `development.md` cites `d1965aa`, now unreachable, with the same subject as `7f13f32`), so if a SHA does not resolve, search `git log --all` by commit subject.
- **Evidence:** entries from 2026-10-17 on are verified against the tree and the docs they cite. Older entries are reconstructed from git history; claims about motive that no commit message or doc states are marked *(inferred)*.
- **State vs. history:** this page records why things changed. For how things work now, read the module pages; for agreed targets, the contracts.

---

## 2026-10-19: batch 1 decisions, contract, wave 1

### Wave 1 close-out (landed in the working tree, uncommitted)
Three parallel tasks plus main-session groundwork, all verified against the tree: `uv run pytest` gives 240 passed and 91 xfailed in about 67 s; frontend `npm test` 7 passed, `npx tsc --noEmit` clean.
- **QA harness and invariant suite (task 1).**
  - `test/conftest.py` and `test/harness/` form one shared, offline harness:
    - process guards (no app DB, loopback-only DNS and sockets, no Ollama outside `live_llm`, temp artifacts, a synthetic applicant);
    - a local HTTP fixture server with synthetic ATS pages;
    - scripted models (`ScriptedLLM`, and `AgentScript` driving the real browser-use loops);
    - a disposable Postgres database per session (`TEST_DATABASE_URL`);
    - a Temporal `WorkflowEnvironment` running the real worker with stubbed browser and LLM activities.
  - Markers `browser`, `db`, `temporal`, and opt-in `live_llm`.
  - `test/invariants/` holds 95 tests keyed to the contract and roadmap A2-A5/A7: 91 are strict `xfail` that the wave-2 owners flip, 4 are guards that already hold. The pure `run_state` rules are tested in `test/yahbah/test_run_state.py`. Full suite at close-out: 240 passed, 91 xfailed.
  - Finding: `job_posting.canonical_url` and `location` exist in the ORM and production DB but in no migration; the harness bridges this (`KNOWN_DRIFT`) until migration 0018 repairs it.
- **Discovery gate (task 2).**
  - Both Gmail pollers do nothing unless their flag is exactly `true`, including the first cycle at startup (the unconditional startup polls are gone).
  - Alert admission: only a job that was scored, at or above `job_alert_min_match_score`, becomes `SCHEDULED`; an unscorable job is `SKIPPED`.
  - Promotion is bounded: `job_alert_max_promotions_per_cycle` (default 5) and `job_alert_max_age_days` (default 14); stale alerts are left alone and stale `SCHEDULED` rows expire.
  - URL intake policy (`url_utils.validate_job_url`, `fetch_public`): public http(s) hosts only, checked on every redirect hop, 2 MiB response cap. It applies at `POST /jobs`, `POST /scheduled` (422), alert links, description fetches and `submit_job_for_application`; the browser agent is not subject to it.
  - Identity: `gh_jid` is kept (it is the job id on company-hosted Greenhouse pages) and `imp_id` is stripped on jobright.ai only.
  - Verification codes: a structural heuristic replaces "first `<h1>`"; boards declare the code shape (`BoardProfile.email_code_pattern`); `GmailClient.wait_for_verification_code` changes nothing in the mailbox and `finalize_code_message` marks it processed once the page accepted the code. `agent/submit.py` still calls the legacy one-step `fetch_verification_code` until task 4 switches.
- **Main-session groundwork.** `src/yahbah/run_state.py` is the contract's code skeleton (vocabulary, `derive_outcome`, `allowed_actions`; the DB functions raise `NotImplementedError` until task 3). `FieldMapping` gained `basis` and `generated` (nothing populates them yet). `BROWSER_USE_VERSION_CHECK=false` stops a PyPI query at every Agent start.
- **Dashboard (task 6, security part).**
  - Job-site HTML is rendered only through `frontend/src/components/untrusted-html-frame.tsx`, a sandboxed iframe with a no-network CSP fed by `frontend/src/lib/untrusted-html.ts`. No component uses `dangerouslySetInnerHTML` any more.
  - `next dev` and `next start` bind 127.0.0.1.
  - The API rejects non-loopback `Host` and `X-Forwarded-Host` headers (`TrustedHostMiddleware` plus a small ASGI check in `src/yahbah/api/main.py`; 13 tests).
  - Frontend tooling: Vitest with Testing Library, ESLint 9 (flat config; the plugins bundled with `eslint-config-next` do not support ESLint 10), and `frontend/scripts/check-untrusted-html-sandbox.mjs`, a real-browser check that the sandboxed frame runs no script and makes no request. A rules-of-hooks bug in `artifact-viewer.tsx` was fixed. The `@types/node` bump (^20 to ^22) awaits the user's OK. `npm run lint` still reports 2 older errors (`react-hooks/set-state-in-effect`) and 6 warnings.
- **Why:** untrusted content must not reach trusted sinks (R5) and the unauthenticated API must not be LAN-reachable (A6). The gate lets the worker start without ingesting months of alerts and launching unreviewed runs. The harness and invariant suite give wave 2 an objective acceptance test.
- **Governs:** [roadmap](../audits/2026-10-18/roadmap.md) (A1, A6, "First batch" tasks 1, 2, 6) and the contract; test setup in [development](development.md).
- **Supersedes:** unconditional startup polls and the `<h1>`-only code heuristic; raw HTML injection of scraped content; `next dev` on all interfaces.
- **Contract revised during the wave:** review rounds (`review_round`, round-matched approve/reject), `actions` on run responses, a uniform error-body shape, the `Blocker` return type of `possibly_applied()`, and migration 0018's repair of the schema drift above.

### Batch-1 contract: run lifecycle, review gate, submission safety
- **What:** [contracts/run-lifecycle.md](contracts/run-lifecycle.md) fixes the shared names and semantics for wave 2:
  - run statuses `AWAITING_REVIEW`, `SUBMIT_UNCERTAIN`, `EXPIRED` and a `failure_reason` vocabulary;
  - the review gate with approve/reject signals;
  - `submission_mode` (`testing | review`; `auto_reviewed` in batch 2) and `origin`;
  - one dedup predicate, `possibly_applied()`;
  - an atomic `claim_submission()`;
  - fenced status writes and browser ownership;
  - retry as a new linked run;
  - server-derived `outcome` and `actions`;
  - migrations 0018 and 0019;
  - per-task file ownership.
- **Why:** the audit's safety failures sat at the boundaries between agents, tasks and the DB, so the boundaries are specified once before parallel work starts.
- **Governs:** the contract itself (owned by the main session; marked TARGET until batch 1 lands). **Source code still shows current behaviour**; the module pages describe current behaviour and point at the contract where it will change.
- **Supersedes (when implemented):** `testing_mode`/`is_test_run`, the worker's RUNNING→FAILED sweep, retry-in-place, dedup on `status == COMPLETED` only, `mark_attempted` (replaced by `claim_submission`).

### Decisions resolved
[roadmap](../audits/2026-10-18/roadmap.md), "Conflicting recommendations: decisions" and "Decisions (resolved 2026-10-19)".
- **Every run passes a review gate (user decision).** Default `review`: fill, save evidence, wait up to 48 h for approval, then re-fill the approved answers and submit; no approval means `EXPIRED`, never auto-submit. `testing` fills only; `auto_reviewed` (a reviewer-model pass) is opt-in, batch 2. *Why:* unattended live submission is not yet safe. *Supersedes:* the boolean `testing_mode`, and Gmail F1's "fill-only for discovered jobs".
- **Worker restart policy is resume.** Remove the RUNNING→FAILED flip in `worker.fail_incomplete_runs`; add heartbeat timeouts. Cancel is how a run is stopped. *Why:* the activities already re-open the browser, and the old flip let a "FAILED" run resume and submit (audit probe B). *Supersedes:* the current FAILED sweep. Until it lands, behaviour is as described in [workflows](modules/workflows.md).
- **Uncertain outcome has one source, three views:** status `SUBMIT_UNCERTAIN`, a `failure_reason` column, a server-derived `outcome`; the frontend holds no status logic. *Why:* "possibly submitted" was being stored as FAILED, so retry and dedup re-applied.
- **Retry creates a new run** (`retry_of_run_id`) and returns 409 if the original attempted submit; retry and cancel stop the old workflow first. *Supersedes:* reset-in-place and the proposed attempt counter.
- **The agent submit guard is action-level**, not network-level: one button classifier shared by `click_submit` and a wrapper over the agent's click tool, plus a post-step tripwire. *Rejected:* blocking non-GET requests (breaks uploads and autosave).
- **Admission: stopgap now, dispatcher later:** explicit activity concurrency and a promotion cap per cycle now; a Temporal PENDING-queue dispatcher once discovery yields jobs.
- **Form-classification ownership is split by layer:** Forms owns the button classifier and agent tool wrapper (batch 1); Navigation's page classifier follows in batch 2. They never edit `agent/tools.py` together.

### Guiding direction: step-aware, multi-approach loops; replace, don't layer
- **What:** the revamp moves from a static pipeline (map the form once, answer once, fill once, one submit) to page-interaction loops that take any job URL, click through to the form, get past or report account walls, walk multi-step flows and answer any field including open-ended curveballs. Every interaction climbs the same verified tier ladder: board-seeded code, one structured model call, an agent loop with vision.
- **Replace, don't layer:** static-paradigm code is deleted when its replacement lands; no parallel old and new paths.
- **Why:** several audit findings are the old paradigm surviving in the new code (roadmap table "Static assumption still in code"). Batch 1's safety work comes first because loops on unfamiliar pages are where a stray submit or false "submitted" happens.
- **Governs:** roadmap "Guiding direction" and B0 (the step-aware application loop, centrepiece of batch 2, spec-first with the main session owning the loop contract).

### Documentation practice
- **What:** this changelog; `CLAUDE.md` "Documentation Maintenance" now requires an entry for every architecturally meaningful change, wiki updates when behaviour changes or text goes stale, and delegates documentation writing to `project-knowledge-maintainer` with main-session review. The `CLAUDE.md` name `project-wiki-manager` was wrong and is corrected.
- **Why:** every audit flagged stale "uncommitted / in flux" text, and several wiki claims had been wrong since the revamp.
- **Also:** a stale-text pass over `architecture.md`, `index.md`, `data-model.md`, `llm.md`, `agent.md`, `workflows.md`; and, at the wave-1 close-out, the README (local-only note, Gmail behaviour, tests, persistent Temporal), `CLAUDE.md` commands, `api-frontend.md` and `development.md`.

---

## 2026-10-18: seven-specialist audit; orchestration model

- **What:** seven specialist agents audited the revamp at `2911263`, each writing a report with reproduction probes; the main session synthesised the [roadmap](../audits/2026-10-18/roadmap.md) (synthesis dated 10-19). Reports are indexed in [docs/audits](../audits/README.md). Seven reports, five findings each, 30 confirmed.
- **Findings that drove priorities:** the revamped pipeline had never run for real (338 runs in the DB are all from before it; 14% function coverage); discovery had produced no usable jobs (all 302 scheduled jobs were jobright.ai aggregator pages; 301 promoted runs failed in one burst); safety invariants failed at boundaries (false "submitted" verdicts, a restart resurrecting a FAILED run, a fabricated cached answer, SPA submit controls passing the guard). Seven shared root causes (R1-R7) are listed in the roadmap.
- **Also (89b1667):** `CLAUDE.md` became the orchestration manual: the main session as Engineering Lead delegating to seven specialist development agents (distinct from runtime LLMs), with task contracts, ownership and acceptance rules.
- **Why:** onboarding audit of a large, untested rewrite before the first live submission.
- **Governs:** audit reports are historical (point-in-time); the wiki and source are authoritative.
- **Supersedes:** the single-session workflow for substantial tasks.

---

## 2026-10-17: agentic, code-first revamp lands; wiki created

### The revamp (`999f57a`, branch `browser-use`)
- **What:** `src/yahbah/agent/` replaces the Playwright/Greenhouse `browser/` package and the LangGraph generic agent.
  - Every browser stage tries deterministic code, then one structured model call, then an agent loop, and verifies each tier's result in code (discover, map form, fill, submit/verify).
  - Board profiles (`agent/boards.py`) hold portal-specific knowledge; Greenhouse and a generic fallback exist.
  - Agents get named tools only, no submit tool and no free-form JS; submit is gated code, recorded before the click.
  - Two model roles on local Ollama: `qwen3-coder-next` (driver: agents, structured calls) and `qwen3.8:27b` (prose, vision, escalation), each with one fixed `num_ctx` so Ollama never reloads; every call is logged per run.
  - Answer mapping: autofill tokens in code, one constrained driver call, prose model for custom questions, an answer cache.
  - Run slots (`max_concurrent_runs`), an email-verified flag from confirmation emails, generalised Gmail code fetch, migration 0017.
- **Why:** `999f57a` ports the browser-use spike and adds what it lacked: fill, gated submit and post-submit verification. Reliable behaviour on arbitrary sites with slow local models needs deterministic code first and models as verified fallbacks.
- **Governs:** [architecture](architecture.md) "Core design rule"; [agent](modules/agent.md).
- **Supersedes / removed:** `browser/` (Greenhouse modules, 1,708-line `greenhouse.py`), `llm/page_analyzer.py`, `config/site_templates/`, Playwright and LangGraph as dependencies. History is kept on `archive/pre-agentic`.

### Docs and wiki (`2911263`)
- **What:** README rewritten for the revamp; `docs/wiki/` created (architecture, module pages, development; progressive-disclosure index); `CLAUDE.md` quick reference and conventions; `.env.example` updated.
- **Why:** the old README described the Playwright/Greenhouse system. Depth lives in the wiki and the README stays a setup guide.
- **Supersedes:** the pre-revamp README.

---

## 2026-10-14: pivot to browser-use

- **What:** `5ae0d05` swapped the dependencies `langgraph`/`langchain-*` for `browser-use==0.13.11` and began tracking `.gitignore`. `7f13f32` committed the LangGraph-era `config/site_templates/{generic,greenhouse}.yaml` and `scripts/spike_browser_use.py` (a Phase-0 spike testing browser-use as the element-discovery/action engine on a Playwright-launched Chromium); the branch `archive/pre-agentic` points at that commit and preserves the pre-revamp code.
- **Why:** the spike tested browser-use as a replacement for hand-built element discovery, and the revamp (10-17) adopted it. *(inferred: no commit states why LangGraph was dropped.)*
- **Supersedes:** the LangGraph generic agent (next entry).

## 2026-10-11: LangGraph generic form agent *(inferred era boundary; two "commit changes before rework" commits)*

- **What:** `5e5b993`/`b72110b` added `browser/generic.py`, a LangGraph state graph (snapshot page -> LLM judge "is the real form visible" -> execute action -> extract fields, max 10 iterations), with `llm/page_analyzer.py` and `generic_form_judge`/`generic_form_analyzer` prompts. `ats_type` (`greenhouse` | `generic`) became a workflow input; the Greenhouse extractor ran first and fell back to the generic agent; generic extraction got a 30-minute timeout. YAML site templates carried per-site hints and a human-approved "confirmed patterns" idea.
- **Why:** support job sites other than Greenhouse (stated in the `generic.py` docstring). It was still LLM-judged and selector-guessing, and the run stayed Playwright-based.
- **Supersedes:** the Greenhouse-only extraction path (kept as the first attempt). Itself superseded by the revamp.

---

## 2026-04-20 to 2026-08-26: job-alert discovery and the scheduled queue

- **What (2026-04-20, `cf9cfcc`, `24272ae`):** a second Gmail poller (`gmail/alert_poller.py`, `alert_parser.py`) detects job-alert emails by sender pattern, extracts job URLs, fetches a description, scores relevance with the LLM and inserts jobs into a `scheduled_job` queue (migration 0016). A promotion step starts a real run after a delay (`job_alert_promotion_delay_hours`, default 24) unless the job is held or skipped. `/scheduled` UI page; shared `jobs.py` entry point used by both `POST /jobs` and promotion; `url_utils.py` reworked for URL normalisation and tracking-parameter stripping.
- **Why:** automate intake from email alerts (hands-off applications); the delay and match-score threshold leave a window to hold or reject. *(inferred from the design; no commit message states it.)*
- **Later:** `145fce4` (2026-08-26, tip of `main`) is frontend error handling only.
- **Governs:** [gmail](modules/gmail.md). The audit found this path had never produced a usable job (see 2026-10-18); the discovery gate (wave 1) and B2/B3 rework it.
- **Supersedes:** manual-only job entry via `POST /jobs`.

## 2026-04-01 to 2026-04-10: product surface around the pipeline

- **What:**
  - Next.js frontend (04-01); dark-cockpit restyle (04-03; branch `ui/dark-cockpit`, an ancestor of `main`, so merged).
  - Prompts moved to `config/prompts.yaml` (04-01).
  - Rich job metadata on `job_posting` (migrations 0004-0013).
  - Testing mode (04-04): fill without submitting.
  - Match score (04-07).
  - Runtime settings in `config/settings.json`, then YAML (04-07, 04-10), editable through the API.
  - Per-run email aliases so replies match their application (04-08).
  - Gmail status poller classifying replies into `application_status_update` (04-07 to 04-10; migration 0015).
- **Why:** observe and control the pipeline from the UI, track outcomes by email, and judge fit before applying. *(inferred; motive not stated in commits.)*
- **Supersedes:** the CLI/log-only view of the first pipeline.

## 2026-03-28 to 2026-03-31: original Playwright/Greenhouse pipeline

- **What:** the initial commit (`d169fcc`) set the architecture that persisted through the revamp: FastAPI control plane, a Temporal workflow `ApplicationWorkflow`, activities for browser (Playwright, Greenhouse-only), LLM (Ollama `llama3.3:70b`: field mapping and cover letter) and DB (Postgres via SQLAlchemy and alembic). Then: autofill, file and cover-letter upload, form validation handling (03-29/30), Gmail API verification-code retrieval (03-30; migration 0003), embedded/iframe form handling, and the first successful Greenhouse submission (`7625295`, 03-31).
- **Why:** a hands-off "Greenhouse job URL in, submitted application out" system on local models.
- **Governs:** the Temporal + FastAPI + Postgres + local-Ollama skeleton is unchanged; the browser, LLM-model and dependency choices were later replaced.
- **Supersedes / superseded by:** the Playwright/Greenhouse browser layer was removed in the revamp (2026-10-17); the single model became the two-role setup.
