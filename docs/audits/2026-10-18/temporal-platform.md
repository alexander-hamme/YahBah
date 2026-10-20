# Temporal & Platform Reliability Audit — temporal-platform-engineer

- **Date:** 2026-10-18
- **Agent:** temporal-platform-engineer (Claude Code development agent)
- **Revision examined:** branch `browser-use` @ `2911263`, plus uncommitted `CLAUDE.md` edit (no source changes)
- **Scope:** `ApplicationWorkflow` and activity options, the worker and its restart behaviour, run state in Postgres, submission idempotency at the workflow/DB level, admission (run slots, scheduled promotion), migrations, platform observability.
- **Status:** Historical point-in-time audit. Findings may become outdated; the wiki and source are authoritative.

## Summary
The basic orchestration design is sound. There is one workflow per run with thin activities. Submit is gated in code, `submit_attempted_at` is written before the click, and inside one workflow a retry only verifies or fails as `submit_unverified`. Migrations form one clean chain (single head `0017`, matching the DB). The guarantees around that design break as soon as anything leaves the happy path:
- **Run status has no single owner.** Four actors write it with no fencing: workflow activities, worker start/stop, API cancel and API retry. As a result restarts, retries and activity timeouts produce resurrected runs, over-admission, and two executors on one run (reproduced).
- **Duplicate protection only covers one run row.** Every cross-run duplicate check treats FAILED, which includes uncertain submits, as "never applied", and treats test runs as "applied".
- **Admission can stall.** The run-slot gate deadlocks under bursts like the 301-job promotion already seen in the DB.
- **Queued work is not durable.** The documented Temporal dev server keeps no history, so queued work disappears on restart.
- **None of this has been exercised.** The agentic pipeline has never run through Temporal, and there are no workflow tests.

## Findings

### F1. A run can have more than one live executor: restart, retry and activity timeouts are not fenced
- **Priority:** Critical
- **Confidence:** Confirmed defect for restart resurrection, over-admission and overlapping timeout attempts (reproduced in probes against the installed SDK 1.20.0). Retry overlap is traced in code. Double submission through these paths is a strongly supported risk.
- **Issue or opportunity:** Four uncoordinated actors write `application_run.status`: workflow activities, worker start/stop, API cancel and API retry. Nothing ties a write, or control of the browser, to the workflow that currently owns the run. Three concrete mechanisms:
  1. **Worker restart.**
     - `fail_incomplete_runs` marks every RUNNING run FAILED on start and on shutdown (`src/yahbah/worker.py:49-78,142`) but leaves the Temporal workflow alive.
     - Temporal then retries the interrupted activity on the new worker. The activities are built for this: `_session_with_form` re-opens the URL (`src/yahbah/workflows/activities/browser.py:108-122`).
     - The next `update_run_state_activity` unconditionally sets `status="RUNNING"` again (`src/yahbah/workflows/activities/db_ops.py:115-124`).
     - In the meantime the FAILED mark freed the run slot, so a queued run starts as well.
  2. **User retry.**
     - The UI offers Retry on any FAILED run (`frontend/src/components/run-actions.tsx:55`).
     - `POST /runs/{id}/retry` resets the same row, overwrites `temporal_workflow_id` and starts a second workflow without stopping the first (`src/yahbah/api/routes/runs.py:340-404`). After a restart the first workflow is still alive (mechanism 1).
     - Two workflows then share one `run_id` and one `SessionRegistry` key. `open()` kills the other workflow's browser, and `get()` hands it the other workflow's browser (`src/yahbah/agent/session.py:39-56`).
     - The submit guard is read-then-write: `already` is read at activity start (`browser.py:288-290`) and `mark_submit_attempted` is a blind UPDATE (`db_ops.py:106-112`). Two executors that both pass the read will both click.
  3. **Activity timeout.**
     - The browser stages and MAP_ANSWERS use `start_to_close=45 min` and `maximum_attempts=2`, with no `heartbeat_timeout` (`src/yahbah/workflows/application.py:65-69`).
     - On this SDK a start-to-close timeout does not stop attempt 1 in the worker. Attempt 2 starts beside it, the workflow continues with attempt 2's result, and both attempts get the same live browser from `SessionRegistry.get(run_id)`.
     - 45 minutes is reachable in the agent tiers: `agent_max_steps=15`, `ollama_timeout=900` s per call, and 300 s email-code waits × `submit_max_attempts=4` (`src/yahbah/config.py:22,33-34,46`).
     - With no heartbeat timeout, a hard-killed worker is only noticed after the full 45 minutes.

  **Cancel.** `cancel_run` writes FAILED immediately (`runs.py:330-335`). The activity only receives the cancellation at its next heartbeat, and heartbeats are sent only at stage boundaries via `beat()` (`src/yahbah/agent/discover.py:143-146`). Meanwhile the workflow's failure path closes the browser under the running activity (`application.py:207-214`).
- **Evidence:**
  - **Probe B** (`/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/temporal-platform-engineer/probe_workflow.py b`).
    - Setup: the real `ApplicationWorkflow` with stub activities registered under the real names, 3 runs, `max_concurrent_runs=1`. The worker was shut down during FILL, the equivalent of `fail_incomplete_runs` was applied, and a new worker was started.
    - Observed: the run was marked FAILED, then `fill attempt=2` started on the new worker. The queued run took the "freed" slot and filled at the same time (`peak_concurrent_RUNNING=2`). The "FAILED" run reached `submit_activity` with DB status back at RUNNING and ended COMPLETED.
  - **Probe C** (`/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/temporal-platform-engineer/probe_timeout.py`).
    - Setup: an activity with a 2 s start-to-close that sleeps 6 s and never heartbeats.
    - Observed: `attempt 2 start; live attempts=[1, 2]`. The workflow returned attempt 2's result while attempt 1 was still running.
    - Variant `probe_timeout_hb.py` (`heartbeat_timeout=1s` plus heartbeats): attempt 1 was cancelled locally at the timeout, with no overlap.
- **Impact:**
  - The UI can show a run as FAILED "Worker restarted" while it is still on its way to submitting.
  - `max_concurrent_runs` is not actually a limit.
  - One click on Retry after a restart puts two automation loops on one application. This is the most plausible duplicate-submission path in the current system.
  - A zombie attempt can drive the browser during a later stage, for example a fix agent still typing while submit clicks.
- **Proposed resolution:**
  - **Pick one restart policy and enforce it in Temporal, not only in Postgres.** Recommended: resume, which the activities already support. Remove the RUNNING→FAILED flip from worker start/stop and set `graceful_shutdown_timeout`. If fail-fast is preferred instead, terminate the interrupted workflows through the Temporal client in the same step.
  - **Add a `heartbeat_timeout` of about 2–3 min** to long activities, with a background heartbeat task in a small activity wrapper. Stage-level `beat()` cannot cover 900 s model calls. Dead workers are then detected in minutes, and timed-out attempts are cancelled locally (as in the probe C variant).
  - **Fence run writes:** `UPDATE … WHERE id=:run AND temporal_workflow_id=:activity.info().workflow_id AND status NOT IN (<terminal>)`. If zero rows change, raise a non-retryable `superseded` error.
  - **Give `SessionRegistry` an owner token** (workflow id + attempt) and a per-run lock, so only the current owner can drive the browser.
  - **Retry** must terminate, or cancel and await, the previous workflow before starting a new one.
  - **Cancel** should request cancellation and let the workflow write the terminal state once activities have stopped.
- **Failure scenario & validating test:**
  - Scenario: the worker restarts during FILL and the run shows FAILED. The user clicks Retry. 45 minutes later the original FILL attempt 2 runs on the retry's browser, and both workflows reach SUBMIT.
  - Tests (`WorkflowEnvironment` with stub activities, as in probes B and C):
    - A restart during each browser stage leaves the run never showing FAILED, and peak RUNNING stays ≤ `max_concurrent_runs`.
    - A retry while the old workflow is alive terminates the old workflow, or its writes are rejected as `superseded`.
    - A start-to-close overrun cancels attempt 1 before attempt 2 touches the session.

### F2. Submission idempotency stops at the run row; uncertain submits look like "never applied"
- **Priority:** Critical
- **Confidence:** Confirmed defect (traced unambiguously in code). Latent today only because no agentic run has reached SUBMIT through Temporal: 0 rows have `submit_attempted_at` set.
- **Issue or opportunity:** `submit_attempted_at` is honoured only by a later `submit_activity` on the same row. Every cross-run "already applied?" decision looks at `status` alone:
  - **CHECK_DUPLICATE.** `check_duplicate_activity` counts only `status == "COMPLETED"` runs (`db_ops.py:255-299`). It ignores FAILED runs that clicked submit and runs that are PENDING or RUNNING, and it does not exclude test runs.
  - **Enqueue-time dedup.** The alert poller and scheduled-add treat every non-FAILED run as applied and every FAILED run as not applied (`src/yahbah/gmail/alert_poller.py:291-300`, `src/yahbah/api/routes/scheduled.py:122-133`). `POST /jobs` has no dedup at all and uses a random workflow ID (`src/yahbah/jobs.py:34-72`), so Temporal's workflow-ID uniqueness provides no idempotency either.
  - **Uncertain outcomes end as FAILED.**
    - Both `submit_unverified` and "browser lost after clicking submit" (`browser.py:294-300`) go through `mark_run_failed_activity`.
    - A later alias-matched confirmation email only sets `email_confirmed_at`; status stays FAILED (`src/yahbah/gmail/poller.py:325-329`). Even a verified application stays invisible to every dedup check.
    - Status polling is currently disabled (`config/settings.yaml:2`), so this confirmation path is not running at all today.
  - **Retry keeps the row and its `submit_attempted_at`** (`runs.py:361-366`).
    - The retry therefore runs `submit_and_verify(already_attempted=True)` against a freshly filled, unsubmitted form, and the judge model decides what happens. A "submitted" verdict marks the run COMPLETED without a click: `contradicts()` does not flag a form that is still present with no errors (`src/yahbah/agent/submit.py:136-144`). A "needs_fix" verdict leads to a click through the fix path (`submit.py:284-291`).
    - The retry also gets a new email alias, because the row's own old alias counts as a collision (`src/yahbah/credentials.py:72-82`). The first attempt's confirmation email can then no longer be matched by alias.
    - `is_test_run` is not reset.
  - **Test runs count as applied everywhere.** A testing-mode run is COMPLETED, so a later real run for the same job is marked DUPLICATE (canonical URL), and the job is blocked from rediscovery.
- **Evidence:** the code references above. Read-only DB aggregates: 6 COMPLETED runs with `is_test_run=true`, and 0 runs with `submit_attempted_at`.
- **Impact:**
  - An application with an uncertain outcome, or one confirmed by email but marked FAILED, is submitted again the next time the same job arrives from any source: alert re-delivery, manual add or promotion.
  - With `max_concurrent_runs>1`, two runs for the same job can both pass CHECK_DUPLICATE and both submit.
  - In the other direction, testing a job blocks the real application to it.
- **Proposed resolution:**
  - **One shared "applied or possibly applied" predicate**, used by CHECK_DUPLICATE, `POST /jobs`, scheduled-add, the alert poller and promotion. It blocks when any non-test run for the same canonical key is PENDING or RUNNING, is COMPLETED, or has `submit_attempted_at` or `email_confirmed_at` set.
  - **A DB guarantee at the click.** Replace `mark_submit_attempted` with an atomic claim in one transaction:
    - `UPDATE application_run SET submit_attempted_at=now() WHERE id=:id AND submit_attempted_at IS NULL AND temporal_workflow_id=:wf`
    - plus an INSERT into a `submission_claim` table that is UNIQUE on the job's canonical key (the canonical URL, otherwise the posting id).
    - Click only if both succeed. A deliberate re-application goes through an explicit user action that releases the claim.
  - **One active run per posting:** a partial unique index on `application_run(job_posting_id) WHERE status IN ('PENDING','RUNNING')`. On conflict, enqueue returns the existing run.
  - **An explicit uncertain status** (for example `SUBMIT_UNCERTAIN`) instead of FAILED. An alias-matched confirmation email moves it to COMPLETED/verified.
  - **Retry** refuses runs with `submit_attempted_at` set unless the user explicitly overrides. Instead of mutating the old row, it creates a new linked run, keeping the old row's alias and evidence intact.
- **Failure scenario & validating test:**
  - Scenario: run R1 clicks submit, and the confirmation page is not recognised in 4 rounds, so R1 ends `submit_unverified` → FAILED. A week later the same posting arrives in another alert. It is not "applied", so it is scheduled and promoted, R2 passes CHECK_DUPLICATE, and a second application is sent.
  - Test 1: seed a FAILED run with `submit_attempted_at` for posting P. Assert that `check_duplicate_activity`, the alert-poller dedup query and `POST /scheduled` all report P as applied. Today none of them do.
  - Test 2: seed a COMPLETED test run. Assert that a real run for the same job is not marked DUPLICATE.

### F3. Run admission can stall completely: the slot gate starves worker activity slots, and promotion is tied to Gmail
- **Priority:** High
- **Confidence:** Confirmed defect. The deadlock was reproduced with a scaled-down slot count; the promotion defects are traced in code.
- **Issue or opportunity:**
  - **The slot gate holds a worker activity slot for the whole wait.**
    - Run slots are acquired by a long-polling activity that occupies a worker activity slot while the run waits: up to 7 days, with unlimited retries (`application.py:79-83`, `db_ops.py:74-103`).
    - The worker leaves `max_concurrent_activities` at the SDK default of 100 (`worker.py:94-119`; `temporalio/worker/_tuning.py:356-360`).
    - With about 100 or more runs queued, every activity slot is held by a waiter. The run that holds the run slot can never get an activity slot for its next stage, so nothing progresses until some waiter's 7-day timeout fires.
    - Each waiter also takes a global advisory-lock transaction every 5 s.
  - **The slot count is derived from `status='RUNNING'`.**
    - It over-admits when restart or cancel flips status early (F1).
    - It leaks permanently when a workflow ends outside the `except ActivityError` path: terminated from the Temporal UI, a workflow-task or nondeterminism failure, or `mark_run_failed_activity` itself failing. Nothing reclaims the slot except a worker restart.
  - **Promotion only runs inside the alert-poller loop.** After the one startup cycle, the loop `continue`s before promoting whenever `job_alert_polling_enabled` is false (`alert_poller.py:78-80`; it is false in `config/settings.yaml:16`). A Gmail error in `poll_for_alert_emails` also skips promotion, because both share one `try` (`alert_poller.py:87-97`). Manually scheduled jobs therefore never auto-promote while alert polling is off or Gmail is failing.
  - **Approved jobs never auto-promote.** Only `status == "SCHEDULED"` is promoted (`alert_poller.py:376-383`). Approving a job (`scheduled.py:240-254`, documented as "approve (still waits for promote_after)" at `scheduled.py:6`) removes it from auto-promotion for good.
  - **Promotion is not atomic.**
    - The poller creates the run and starts the workflow, then writes `PROMOTED` in a separate session (`alert_poller.py:394-401`). The API `/promote` route uses the same pattern and can race the poller (`scheduled.py:290-315`).
    - A crash or DB error between the two steps re-promotes the job on the next cycle.
    - While Temporal is unreachable, every cycle leaves another orphan PENDING run per job, because `jobs.py:63-72` commits before `start_workflow`.
- **Evidence:**
  - **Probe A** (`/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/temporal-platform-engineer/probe_workflow.py a`): the real workflow with stub activities that complete instantly.
    - `slots=10 runs=6`: all 6 runs COMPLETED.
    - `slots=4 runs=6`: after 12 s all 6 were still RUNNING in Temporal. The DB showed one run RUNNING/STARTING (holding the run slot) and the rest PENDING/QUEUED. The slot holder never got to run its next activity.
  - **History:** on 2026-06-15, 301 scheduled jobs from a single alert source were promoted in one cycle, within 25 s (DB: 301 runs created between 16:23:22 and 16:23:47). The pre-revamp code had no run-slot gate, so they ran concurrently and each failed in about 25 s. Under the current gate the same burst would produce about 300 waiters, three times the deadlock threshold.
- **Impact:**
  - One large alert digest, or a backlog after downtime, freezes all applications for days with no error anywhere.
  - Approved jobs silently never run.
  - The scheduled queue only works while Gmail is healthy.
- **Proposed resolution:**
  - **Stopgap:** set `max_concurrent_activities` explicitly and cap promotions per cycle.
  - **Proper fix: move admission out of activity slots.** Recommended design:
    - PENDING `application_run` rows become the durable queue.
    - A single dispatcher starts `ApplicationWorkflow` only while RUNNING < N. It can be a loop hosted in the worker and guarded by an advisory lock, or a Temporal schedule.
    - The workflow ID stays `application-{run_id}`, so a repeated start is idempotent.
    - The workflow no longer needs its 7-day waiting activity.
    - The dispatcher is the natural home for the planned ranking by match score and max-K per company.
  - **Temporal-only alternative:** a short try-acquire activity plus a signal-driven `workflow.wait_condition` with a periodic re-check.
  - **Promotion:**
    - Run it on its own timer, independent of Gmail.
    - Promote APPROVED jobs too, or define approve as "promote now".
    - Make the SCHEDULED/APPROVED→PROMOTED transition a conditional `UPDATE … RETURNING` in the same transaction that inserts the run.
- **Failure scenario & validating test:**
  - Admission test: a workflow test with `max_concurrent_activities=k` and k+2 queued runs. All runs complete, and peak RUNNING never exceeds N.
  - Promotion test: with alert polling disabled, a due SCHEDULED or APPROVED job is promoted exactly once, including when `/promote` is called at the same time.

### F4. Durable execution isn't durable in the documented setup, and nothing reconciles Postgres with Temporal
- **Priority:** High
- **Confidence:** Confirmed defect (configuration observed; orphan paths traced in code)
- **Issue or opportunity:** README, the wiki and CLAUDE.md all start Temporal with `temporal server start-dev` and no `--db-filename` (`README.md:120`, `docs/wiki/development.md:4`, `CLAUDE.md:25`). The running server was started this way, so its state lives in memory. Any server restart (reboot, closed terminal) deletes every queued and in-flight workflow, including the 7-day slot waits. Nothing reconciles `application_run` against Temporal:
  - **Orphaned PENDING runs are never cleaned up.** `fail_incomplete_runs` deliberately skips them on the assumption that Temporal will resume them (`worker.py:50-51`).
  - **Orphaned RUNNING runs hold the run slot** until the next worker restart.
  - **Orphaned PENDING runs block discovery.** The alert poller and scheduled-add count them as "already applied", so that job is excluded from discovery for good (`alert_poller.py:291-300`).
  - **Failed workflow starts leave PENDING rows.** `submit_job_for_application` commits the PENDING row before `start_workflow` and leaves it in place if the start fails (`jobs.py:63-72`). `retry_run` already handles this case correctly (`runs.py:394-398`).
- **Evidence:**
  - The running process's command line is `temporal server start-dev` with no flags.
  - `temporal workflow list` against it returns nothing, although Postgres holds 338 runs (all from before the revamp).
  - The orphan paths are in the code references above.
- **Impact:** Routine machine restarts silently drop queued work and leave rows that block rediscovery. The architecture's main guarantee, "Temporal for durable orchestration" (`docs/wiki/architecture.md:3`), does not hold on the deployment the project documents.
- **Proposed resolution:**
  - Document and use `temporal server start-dev --db-filename <path outside the repo>`.
  - Add a periodic reconciler to the worker. For each PENDING or RUNNING run, `describe` its `temporal_workflow_id`. If the workflow is missing or closed while the DB row is non-terminal:
    - re-dispatch a PENDING run that never started;
    - otherwise mark the run as interrupted, with the reason;
    - if `submit_attempted_at` is set, use the uncertain status from F2 instead.
  - With F3's dispatcher, a failed workflow start no longer leaves an orphan, because PENDING rows are the queue.
- **Failure scenario & validating test:**
  - Scenario: enqueue 3 jobs with `max_concurrent_runs=1`, then restart the dev server. Today 2 rows stay PENDING/QUEUED forever, and their URLs are blocked from discovery.
  - Test: run the reconciler against a stub Temporal client that returns not-found, and assert the expected state transitions.

### F5. The Temporal path of the agentic pipeline has never run, has no tests, and leaves a thin operator record
- **Priority:** Medium
- **Confidence:** Confirmed defect (absence verified)
- **Issue or opportunity:**
  - **No workflow tests.** No test uses `temporalio.testing` or touches the workflow or its activities; the 40 existing tests cover agent and LLM logic only.
  - **The revamped pipeline has never run through Temporal.** It has only been run through `scripts/run_pipeline.py`, which bypasses Temporal, the DB run record and `mark_submit_attempted` (`scripts/run_pipeline.py:125`).
  - **Nothing records steps.** `application_step` is never written, so `GET /runs/{id}/steps` and the UI step timeline are always empty.
  - **The per-run record is thin.** The DB holds only `current_state`, `updated_at` and `str(exc.cause)`. There is no record of attempts, timeouts, retries, or which worker ran what.
  - **Retry destroys evidence.** It deletes the run's step and artifact rows and clears its error (`runs.py:361-379`), including the evidence of a possibly-submitted first attempt.
  - **No versioning or replay protection** for workflow changes, although the workflow is still changing and queued executions can live for days.
- **Evidence:**
  - `test/yahbah/**` contains no workflow tests.
  - All 4 entries in `artifacts/_observations.jsonl` are `debug-*` runs.
  - The newest workflow-run artifact directory is from 2026-06-16.
  - All 338 DB runs use the pre-revamp stage names (EXTRACT_FORM, FILL_AND_SUBMIT, MAP_FIELDS).
  - `application_step` has 0 rows, and nothing in `src/` constructs one.
  - As far as the evidence shows, the probes in this audit were the first time this `ApplicationWorkflow` executed against a Temporal server.
- **Impact:** F1–F4 would otherwise first show up on a real application. From the app alone, an operator cannot answer "was submit attempted, by which attempt, and why did it stop?"
- **Proposed resolution:**
  - **Workflow test suite.** Add `test/yahbah/workflows/` with stub activities registered under the real names. Probes A, B and C are a starting point; `start_local(dev_server_existing_path=…)` avoids downloading a server. Cover: happy path, testing mode, duplicate, non-retryable stage failure, restart mid-stage, timeout overlap, cancel, retry and burst admission.
  - **Replay protection.** Once real runs exist, keep a few of their histories and run them through `Replayer` before deploying workflow changes. Use `workflow.patched()` for changes to sequences that may be in flight.
  - **Run event log.** Write one event per stage attempt, reusing `application_step`: stage, attempt, start/end, outcome, error type, retryable. Never delete events on retry.
  - **First Temporal end-to-end run.** Before the first unattended real submission, do one end-to-end run through Temporal in testing mode on the offline fixture.

## Documentation discrepancies
- **Restart behaviour.** `docs/wiki/architecture.md` ("a worker restart marks RUNNING runs FAILED") and `docs/wiki/modules/workflows.md` ("PENDING runs are left for Temporal to resume") imply that RUNNING runs end at a restart. In fact Temporal resumes them, and they come back as RUNNING (F1, probe B).
- **Stale "uncommitted" notes.** `docs/wiki/index.md` ("uncommitted work in tree") and the `architecture.md` "In flux" section (migration 0017 untracked, agent package uncommitted) are out of date. `0017` and `src/yahbah/agent/` are tracked, and alembic has a single head at 0017, matching the DB.
- **Step tracking.** `modules/data-model.md` and `modules/api-frontend.md` describe `application_step`, `/runs/{id}/steps` and a step timeline as live. Nothing writes steps.
- **Temporal persistence.** `README.md:120` and `development.md:4` give a non-persistent Temporal command, while the architecture claims durability (F4).
- **Duplicate runs (minor).** `modules/workflows.md` says duplicate runs "raise CancelledError". Temporal records these as Failed workflows, not Cancelled, because the SDK turns an un-requested CancelledError into a workflow failure (`temporalio/worker/_workflow_instance.py:2412-2425`).

## Cross-area notes
- **application-forms-engineer:** verify-only mode (`already_attempted=True`) can still click submit through the `needs_fix` and `email_code` branches. `contradicts()` also does not flag a "submitted" verdict while form fields are still present with no errors (`src/yahbah/agent/submit.py:136-144,251-291`). This matters for retries on a freshly re-filled form (F2).
- **gmail-job-discovery-engineer:** a retry changes the run's email alias (`src/yahbah/credentials.py:72-82`), so confirmations of the first attempt no longer match by alias. A matched confirmation never changes a FAILED status (`src/yahbah/gmail/poller.py:325-329`). Dedup should use the shared predicate from F2.
- **product-dashboard-engineer:** Retry is offered for every FAILED run regardless of `submit_attempted_at` (`frontend/src/components/run-actions.tsx:55`), and `submit_attempted_at` is never shown. The uncertain-submission state needs its own UI and a guarded retry.

## Proposed action plan
1. **Run ownership and the submission claim (F1, F2).** Tie run writes and browser sessions to the owning workflow. Make the submit claim an atomic DB claim per job. Add the uncertain status and the shared dedup predicate. Make retry and cancel stop the old workflow. Outcome: no path can produce two submit clicks for one job, and uncertain submissions are never silently re-applied.
2. **Restart, timeout and durability policy (F1, F4).** Resume on restart; add heartbeat timeouts with a background heartbeat; persist the dev server; add a Postgres↔Temporal reconciler. Outcome: worker and Temporal restarts lose no work and leave no divergent rows.
3. **Admission redesign (F3).** A dispatcher starts workflows from the PENDING queue. Promotion runs independently of Gmail, includes APPROVED jobs, and is atomic. Outcome: bursts queue instead of deadlocking, and ranking and per-company caps have a natural place to live.
4. **Workflow test harness and run event log (F5).** Build these first and alongside 1–3. Outcome: each fix lands with a fault-injection test, and operators can see attempts and why a run stopped.
- **Dependencies / collaboration:**
  - application-forms-engineer: the claim call inside `submit_and_verify` and the verify-only semantics.
  - gmail-job-discovery-engineer: the shared dedup predicate and moving promotion out of the poller.
  - product-dashboard-engineer: the uncertain state and guarded retry/cancel UX.
  - browser-agent-qa-engineer: the fault-injection suite.
  - New status values and the claim table are a shared-contract change that the main session must coordinate.
- **Defer:**
  - Multi-worker deployment: the in-process `SessionRegistry` would need per-worker task queues for session affinity.
  - OpenTelemetry or metrics export.
  - Any heavier distributed-concurrency infrastructure.

  None of these matters at `max_concurrent_runs: 1` on one machine.
