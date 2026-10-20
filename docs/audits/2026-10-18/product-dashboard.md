# Product & Dashboard Audit — product-dashboard-engineer

- **Date:** 2026-10-18
- **Agent:** product-dashboard-engineer (Claude Code development agent)
- **Revision examined:** branch `browser-use` @ `2911263`, plus uncommitted `CLAUDE.md` edit (no source changes). (That edit is now committed as `89b1667`, CLAUDE.md only, so the source is the same.)
- **Scope:** Next.js frontend (`frontend/src`) and the FastAPI routes and contracts it consumes (`src/yahbah/api/`), including how run outcomes, evidence, controls and settings are presented.
- **Status:** Historical point-in-time audit. Findings may become outdated; the wiki and source are authoritative.

## Summary
The dashboard is still the pre-revamp UI. Commit 999f57a changed the frontend in one way only: it added the `email_confirmed_at` badge. Every application in the DB (338 runs, newest 2026-06-15) predates the agentic pipeline, so none of the new contracts has yet been rendered by a real run. Several of them will render wrong on the first run: the new `submitted_values` shape, the new screenshot labels, `trace.jsonl`, and submit tracking. The bigger problem is trust. The UI cannot tell a test run from a real submission, or an uncertain submission from an ordinary failure. Retry can start a live submission with one click and deletes the evidence of the earlier attempt. Confirmation HTML scraped from the job site is injected raw into an unauthenticated dashboard that anyone on the LAN can reach. The scheduled queue is usable for triage but cannot show what happened to the jobs it promoted: all 301 promoted jobs failed, and that is invisible on the page. Settings, profile editing and review-before-submit are missing the API foundations they need. Neither `archive/pre-agentic` nor `ui/dark-cockpit` has anything to recover (see Defer).

## Findings

### F1. Submission outcomes are misrepresented, and Retry can start an unconfirmed live submission while deleting the prior attempt's evidence
- **Priority:** Critical
- **Confidence:** Confirmed defect (traced in code; state observed in DB)
- **Issue or opportunity:** The frontend shows outcomes using only `status`. It does not use `is_test_run` or `submit_attempted_at` (the only use of `is_test_run` is to enable Retry). The retry API ignores submit-tracking state.
- **Evidence:**
  - Test runs look like real submissions. Testing mode ends the run as `COMPLETED` (`workflows/activities/browser.py:267-276`). The pipeline then treats any `COMPLETED` run as submitted (`application-status-pipeline.tsx:267`: `const submitted = runStatus === "COMPLETED"`), and draws a "Submitted" stage. The list and the status badge show "Completed" with no test marker (`status-badge.tsx`, `application-list.tsx:187`). The DB holds 6 `COMPLETED` test runs.
  - Uncertain outcomes look like ordinary failures. `submit_unverified` and other failures that happen after the submit click become `FAILED` with an error string (`browser.py:323-327`, `db_ops.py:142-153`). `submit_attempted_at` is part of `RunDetail` (`types.ts:67`) but no frontend code reads it. The default list filter hides all `FAILED` runs (`applications/page.tsx:34,51-52`), and the FAILED view offers "Delete all".
  - Retry semantics (`api/routes/runs.py:340-404`):
    - The endpoint allows retrying any test run, including `COMPLETED` ones (`:351`). Testing mode is currently off (`GET /settings` → `testing_mode: false`), so retrying a test run performs a real submission.
    - In the list, Retry is an icon with no confirmation (`run-actions.tsx:55,77-89`) and no indication that the mode is LIVE.
    - Retry deletes the previous steps and artifacts (`:368-379`): screenshots, confirmation, submitted values.
    - Retry does not reset `is_test_run`, `confirmation_html`, `submit_attempted_at` or `email_confirmed_at`, and nothing else ever sets `is_test_run` back to false (grep: the only write is `browser.py:271`). A real submission made through a retried test run therefore stays flagged as a test, and Retry stays offered on it.
  - Cancel records `FAILED` / "Cancelled by user" whether or not submit was already clicked (`runs.py:330-335`).
- **Impact:** The dashboard is the user's record of what was actually sent to employers, and it violates the project invariant that attempted, submitted, verified, failed and uncertain outcomes must be distinguished. A misclick on a test-run row in LIVE mode submits a real application. Possibly-submitted runs are hidden by default and can be bulk-deleted or retried. Retrying erases the evidence of the earlier attempt.
- **Proposed resolution:**
  - Add a server-computed `outcome` field to the list and detail responses, so the frontend keeps no business logic. Values: `in_progress | test_only | submitted | email_verified | submit_uncertain | failed_before_submit | cancelled | duplicate`, derived from status, `is_test_run`, `submit_attempted_at` and `email_confirmed_at`. Badges, the pipeline, filters and stats should render from it.
  - Retry should return 409 when `submit_attempted_at` is set. Recovering those runs needs an explicit, separate "confirm not submitted" action.
  - Retry should reset the test-run fields, and it should keep the earlier attempt's evidence: either an attempt counter on artifacts, or a new run row linked to the original.
  - Every action that can lead to a submission (Retry, Promote, test-run re-run) should get a confirmation that names the current TESTING/LIVE mode.
  - Coordinate the semantics with temporal-platform-engineer.
- **Failure scenario & validating test:**
  - Scenario: with LIVE mode, call `POST /runs/{test_run}/retry` and the workflow reaches SUBMIT. Expect 409, or a run whose `outcome` becomes `submitted` with `is_test_run` cleared.
  - API contract test: a FAILED run with `submit_attempted_at` set must expose `outcome == "submit_uncertain"`, and retry on it must return 409.
  - Component test: a COMPLETED test run must not render a "Submitted" stage.

### F2. Untrusted job-site HTML is injected raw into the dashboard, and the dashboard proxies an unauthenticated API to the LAN
- **Priority:** High
- **Confidence:** Confirmed defect (sink and source traced; LAN reachability observed)
- **Issue or opportunity:** Content from third-party job sites runs inside the dashboard's origin, and that origin has full control over submissions. The origin is also reachable from other devices on the network.
- **Evidence:**
  - Source: on submit, the confirmation HTML is the site's raw `main`/`body` `innerHTML.slice(0, 4000)` (`agent/submit.py:101,271`). It is stored in `application_run.confirmation_html` (`browser.py:337-341`).
  - Sink: `run-info-card.tsx:221-224` renders it with `dangerouslySetInnerHTML`, with no sanitizing. Inline handlers (`<img onerror>`, `<svg onload>`), `javascript:` links and iframes all work there. All 9 stored confirmation HTML values contain remote `<img>` tags. They are hidden by CSS but still fetched, so every view makes requests to third-party hosts.
  - Exposure:
    - `npm run dev` is plain `next dev` (`package.json:6`), which binds to 0.0.0.0 by default (bundled docs: `node_modules/next/dist/docs/01-app/03-api-reference/06-cli/next.md:71`). The live process listens on `*:3000`.
    - `next.config.ts` rewrites `/api/*` to `localhost:8000`, so the API is reachable through that port. `GET http://<LAN-IP>:3000/api/health` returned 200.
    - The API has no auth on any route. That includes `PUT /settings/{key}` (switching to LIVE), `POST /jobs`, `/scheduled/{id}/promote`, `/runs/bulk-delete`, the artifact downloads (resume, cover letters, screenshots), and `POST /gmail/alert-backfill`.
  - What is fine: uvicorn binds to 127.0.0.1. CORS is limited to `localhost:3000`, which does not matter for the UI because it goes through the same-origin proxy. Artifact download looks rows up by UUID and has no path input, so there is no traversal.
  - Minor CSRF surface: POSTs without a body (cancel, retry, approve, promote, `/gmail/poll`) skip preflight, but all except the Gmail triggers need UUIDs an attacker cannot know.
- **Impact:**
  - A hostile or compromised careers page, or merely an inline handler on one, can run script that switches the system to LIVE, enqueues or promotes applications, deletes records, or reads applicant data.
  - Anyone on the same Wi-Fi can do the same without any XSS.
  - This breaks the invariants about untrusted webpage content and applicant data privacy.
- **Proposed resolution:**
  - Never inject the HTML. Show `confirmation_text`, which already exists as an artifact, or render the HTML in `<iframe sandbox srcdoc>` with no `allow-scripts` and a CSP that blocks remote images.
  - Change the dev script to `next dev -H 127.0.0.1`.
  - Add `TrustedHostMiddleware` (localhost, 127.0.0.1) to blunt DNS rebinding.
  - Before any LAN or remote use, add a local API token checked in FastAPI middleware.
- **Failure scenario & validating test:**
  - Component test: render `RunInfoCard` with `confirmation_html` set to `<img src=x onerror="window.__x=1">`. `window.__x` must stay undefined and the DOM must contain no `img`.
  - Smoke check: the dev server must not answer on the LAN IP.

### F3. The UI fell behind the revamp's evidence and observability contracts; the next real run will render blank or misleading evidence
- **Priority:** High
- **Confidence:** Confirmed defect
- **Issue or opportunity:** The frontend reads artifact JSON and screenshot labels by hand, using string conventions that the revamp changed. It has no typed contract and no tests, so `tsc` passes (exit 0) while the evidence views break.
- **Evidence:**
  - **Submitted values:**
    - `agent/fill.py:636-644` now writes rows of `{key, label, type, required, source, written, page_value, ok}`.
    - `field-mappings-table.tsx:17-21,87-95` still reads `{form_label, mapped_to, value}`. Every row of a new run will render blank.
    - The read-back verification (`ok`, written versus what the page shows) is never displayed, though it is the strongest evidence of exactly what was submitted.
    - All 27 stored artifacts use the old shape. No workflow run has happened since the revamp: no `trace.jsonl` exists under `artifacts/`, and the newest run is 2026-06-15.
  - **Screenshots:**
    - The revamp's labels are `form_found`, `discover_failed`, `map_failed`, `before_fill`, `fill_failed`, `before_submit`, `submit_failed` and `confirmation` (`browser.py:173-329`).
    - `artifact-viewer.tsx:57` prefers the retired `extract_form` and `after_auth`. Outside dev mode, the failure screenshots from discover, map and fill are never shown.
    - The outcome slot says "Submission Pending" for failed runs and for test runs (`:345`).
  - **Timeline:** Nothing writes `application_step`. The table has 0 rows ever and there is no writer in `src/` (grep), so `StepTimeline` and `GET /runs/{id}/steps` are dead.
  - **Unexposed diagnostics:**
    - The revamp's per-stage trace (`{stage, method, secs, note}`, `agent/discover.py:130-140`) is written only to disk (`browser.py:79-86`), and so are the per-run LLM logs. Neither is registered as an artifact.
    - Live progress already exists as Temporal heartbeat details (`discover.py:143-146`, `submit.py` `beat(...)`) but is not exposed.
    - The structured failure reason (`StageFailed.reason`, e.g. `needs_answer`, `login_wall`, `captcha`, `submit_unverified`; `discover.py:120-127`) is flattened into `error_message` text.
  - **Typing:** `types.ts:116-121` `FieldMapping` is stale and unused. `ArtifactResponse.metadata` and the artifact JSON payloads are untyped.
  - **Lint:** there is no ESLint and no `lint` script, so the hook called after an early return (`artifact-viewer.tsx:260,274`, rules-of-hooks) goes undetected. It is latent only because the parent page guards against an empty list.
- **Impact:** The user cannot see what each stage did, which tier (code, model or agent) it needed, why a run failed beyond a raw string, or what was actually typed into the form. Those are the core supervision questions. Without a structured reason, the UI also cannot offer the right next action (answer a question, log in, solve a CAPTCHA, retry).
- **Proposed resolution:**
  - Give artifact payloads typed response models: a `submitted_values` row model, plus an endpoint `GET /runs/{id}/trace` that returns trace rows. Persist `failure_reason` as its own column, coordinated with temporal-platform-engineer.
  - Generate TypeScript types from FastAPI's OpenAPI. This needs a dev dependency and the user's approval. Until then, keep hand-written types next to contract tests.
  - Render submitted values as written versus on-page, with an ok flag. Map the new screenshot labels and show the failure screenshot in the outcome slot. Replace the step timeline with the trace (stage, method, duration, note) plus a live heartbeat line.
  - Add ESLint (`eslint-config-next` with react-hooks).
- **Failure scenario & validating test:**
  - API contract test (pytest with `httpx.ASGITransport`, which already works without a Temporal lifespan; verified during this audit): seed one run with revamp-shaped artifacts and check the response schemas.
  - Component test: a `submitted_values` fixture in the new shape renders labels, written and on-page values.

### F4. Destructive and consequential actions fail silently; deletion is broken for 301 of 306 failed runs
- **Priority:** Medium
- **Confidence:** Confirmed defect
- **Issue or opportunity:** An FK blocks deleting most runs. Separately, the frontend swallows request errors, so the user cannot tell whether an action happened or the backend is down.
- **Evidence:**
  - **FK blocks deletion:**
    - Migration 0016 created `scheduled_job.promoted_run_id` with `ON DELETE NO ACTION` (catalog: `confdeltype='a'`). `ScheduledJob` has no ORM relationship to the run.
    - `DELETE /runs/{id}` and `/runs/bulk-delete` (`runs.py:271-300,407-442`) therefore fail with an FK violation, returned as a 500, for every run promoted from the queue.
    - That covers 301 of the 306 FAILED runs. "Delete all failed (306)" fails as a single unit.
  - **Errors swallowed:**
    - `RunActions` mutations have no `onError` (`run-actions.tsx:22-52`).
    - The scheduled `RowActions.act` has no catch (`scheduled/page.tsx:313-321`).
    - The scheduled add form discards errors (`:193` `// TODO: toast`), so a 409 "already applied" never reaches the user.
    - `handleBulkDelete` leaves its rejection unhandled (`applications/page.tsx:87-92`).
    - On query errors, the applications page renders nothing (`:162-181`) and the scheduled page says "No scheduled jobs yet".
    - `TestingToggle` shows "LIVE" when settings fail to load (`testing-toggle.tsx:23`).
  - **Observed live:** the API process on :8000 (started 2026-10-16) returns 500 for every DB-backed GET. The same code called in-process through the ASGI transport returns 200. The cause is unknown (logs were not accessible), and the dashboard does not show the outage.
  - **Unguarded one-click controls:** the trash and cancel icons in list rows, and on the scheduled page Delete and "Promote now" (which starts an application immediately).
- **Impact:** The user's cleanup actions do nothing and give no feedback. Outages and rejected actions look like empty data. Promote, which can submit, has no guard.
- **Proposed resolution:**
  - Add a migration making `promoted_run_id` `ON DELETE SET NULL` (temporal-platform-engineer owns migrations), or null the reference inside the delete routes. Consider soft-delete or archive for runs, so evidence and duplicate protection are not lost.
  - Add a single mutation error surface (a toast or inline message) and explicit query error and stale states (for example "API unreachable, last updated hh:mm"). Show "unknown" rather than LIVE when settings fail to load.
  - Add confirmation to the compact delete, cancel and promote controls.
- **Failure scenario & validating test:** API test: delete a run referenced by a `scheduled_job`. Expect 200, with the reference nulled or archived. UI test: a mocked 500 on mutation shows an error message.

### F5. Supervision foundations are missing for the scheduled queue, human answers and settings (readiness for the planned work)
- **Priority:** Medium
- **Confidence:** Strongly supported risk
- **Issue or opportunity:** The planned features need API and UI foundations that do not exist yet: the scheduled queue as a core page, rescuing auto-excluded jobs, review before submit, editing profile and prompts, and settings in a sidebar.
- **Evidence:**
  - **Promoted jobs have no visible outcome:**
    - `ScheduledJobItem` has `promoted_run_id` but not the run's outcome. The page shows a violet "Promoted" with no link to the run (`scheduled/page.tsx:580`).
    - In the DB, 301 jobs are PROMOTED, and all 301 of their runs FAILED with the same "Page does not appear to be a job application" error.
    - Both pages hide this: the applications page hides failures by default, and the scheduled page only says "Promoted".
  - **Skipped jobs cannot be rescued:**
    - The API allows approving SKIPPED jobs (`scheduled.py:249`), but the UI hides Approve for them (`scheduled/page.tsx:328`). That is exactly the planned "rescue skipped" flow.
    - `match_rationale` is returned but never shown.
    - `EXPIRED` has no filter.
  - **Human answers:**
    - `needs_answer` is a terminal `FAILED` with a string (`field_mapper.py:118-123`). Known answers come from `personal.yaml` through an `lru_cache` (`config.py:121`), so fixing an answer means editing the file and restarting the worker.
    - The revamp's `answer_cache` (2 rows) has no API, so a wrong cached answer is reused silently on every future application.
    - The workflow has no signal or pause point for review before submit (`workflows/application.py`).
  - **Settings:**
    - `PUT /settings/{key}` accepts any key and any JSON value, with no schema or validation (`api/main.py:56-61`, `config.py:111-118`).
    - Writes are unlocked read-modify-write cycles on the git-tracked `config/settings.yaml`, resolved relative to the working directory (`config.py:90`).
    - If the file is missing, the code falls back to `testing_mode: false`, i.e. LIVE (`config.py:93-97`).
    - The frontend types settings as just `{testing_mode: boolean}`.
  - **Single profile:** the profile is loaded with `select(ApplicantProfile).limit(1)` (`browser.py:66-71`), and no table has a user or profile key.
- **Impact:** The discovery pipeline can fail every job it finds without the user noticing. The most common case that needs a human, a missing answer, has no human-in-the-loop path. A settings UI built on the current endpoint would let invalid values reach the worker, and a missing file would fail open to LIVE.
- **Proposed resolution:**
  - Add the promoted run's `outcome` (from F1) and a link to `ScheduledJobItem`, and enable Approve for SKIPPED jobs.
  - Add a "Needs you" queue backed by a structured `failure_reason == needs_answer`, with the question exposed. Answering writes `answer_cache` or the known answers, then retries. Cache semantics are owned by llm-intelligence-engineer.
  - Add a typed `RuntimeSettings` Pydantic model with per-key validation and an "applies to: immediately / next run" annotation. Make a missing settings file default to TESTING.
  - Defer multi-profile work until a profile id exists on runs.

## Documentation discrepancies
- `docs/wiki/modules/api-frontend.md`:
  - It leaves out `POST /gmail/alert-backfill`, which the frontend uses.
  - It says response models live in `schemas.py`. That file holds the Temporal activity dataclasses; the API models live in the route files.
  - "both modified in working tree" is stale.
- `api-frontend.md` lists a "step timeline". README:161 says `trace.jsonl` and the LLM logs can be listed through the artifacts endpoint. In fact nothing writes `application_step`, and neither `trace.jsonl` nor the LLM logs are registered as artifacts.
- README:123-126 implies the setup is local-only (the CORS note). In fact the frontend dev server binds to 0.0.0.0 and proxies the API.
- `docs/wiki/modules/data-model.md:7` says migration 0017 is "untracked". It is committed, and the DB is at 0017.

## Cross-area notes
- **application-forms-engineer:** `submit_and_verify(already_attempted=True)` can still click submit inside the verify loop (`agent/submit.py:289-291`), so "verify only" does not guarantee no further clicks after Retry re-fills a fresh page.
- **temporal-platform-engineer:**
  - The duplicate check counts only `COMPLETED` runs (`db_ops.py:262,284`). As a result, COMPLETED test runs block real applications, while FAILED runs with `submit_attempted_at` set do not block.
  - Cancel marks a run `FAILED` immediately (`runs.py:330-335`), but the workflow's cancellation path skips `close_session_activity` and `mark_run_failed`.
- **gmail-job-discovery-engineer:**
  - All 301 promoted alert jobs failed the same "not a job application" check.
  - Manual promote and the auto-promoter share no row lock (`scheduled.py:290-315` vs `alert_poller.py:366-400`).
  - A failure to start the workflow after the run row is committed leaves an orphan PENDING run (`jobs.py:34-68`).

## Proposed action plan
1. **Make outcomes truthful and Retry safe:** add a server-derived `outcome` field, make Retry return 409 after a submit attempt, reset the test fields and preserve evidence, and add confirmations that name the LIVE/TESTING mode. Outcome: the dashboard becomes a trustworthy record of what was sent, and no single click can submit unintentionally.
2. **Close the security boundary:** render confirmation content without injecting HTML (text or a sandboxed iframe), bind the dev server to 127.0.0.1, and add `TrustedHostMiddleware`. Outcome: page content cannot drive the API, and the network cannot reach applicant data.
3. **Catch up the evidence contract:** typed artifact and trace endpoints, a structured `failure_reason`, new-shape submitted values, the new screenshot labels and a trace timeline, plus API contract tests and ESLint. Outcome: the first agentic run shows exactly what was written, verified and why it failed.
4. **Fix the delete FK and add error surfacing,** then link scheduled jobs to their run outcome. Outcome: cleanup works, outages and rejected actions are visible, and failures in the discovery pipeline cannot hide.
- **Dependencies / collaboration:**
  - temporal-platform-engineer: outcome semantics, the `failure_reason` column, the FK migration, cancel behaviour.
  - application-forms-engineer: a guarantee that verify-only mode never clicks.
  - gmail-job-discovery-engineer: promoted-run status and promote locking.
  - llm-intelligence-engineer: semantics of the answer-cache review API.
  - browser-agent-qa-engineer: API contract and component tests, using a seeded DB and no submissions.
- **Defer:**
  - Framer Motion and visual polish; the sidebar layout. Do these after the settings API from F5 has a typed contract.
  - Multi-profile support.
  - Replacing Google favicon fetches (`company-logo.tsx:7`) with cached or local logos. This addresses privacy (the list of companies is sent to Google) and icon-loading lag.
  - WIP recovery: nothing to recover. `archive/pre-agentic` and `ui/dark-cockpit` are strict ancestors of `browser-use` (each branch tip is the merge-base). Their delete buttons, `StatusBadge`, `FailedBadge` and `DeleteAllButton` already exist on `browser-use`.
