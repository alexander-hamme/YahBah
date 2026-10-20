# Workflows, Activities, Worker

Implements the run lifecycle of [contracts/run-lifecycle.md](../contracts/run-lifecycle.md) (batch-1 task 3): statuses, the review gate, the submission claim, fenced writes, browser ownership, resume on restart. Status, reason, mode and outcome vocabularies and the dedup predicate live in `src/yahbah/run_state.py`.

## Workflow
`ApplicationWorkflow` in `src/yahbah/workflows/application.py`, one per `application_run`.
- **Input:** `ApplicationWorkflowInput(run_id, job_url, submission_mode, origin, review_window_hours)`. The mode and window are snapshotted when the run is created (`jobs.submit_job_for_application`, from `config.get_effective_runtime_settings()`), so later settings changes affect new runs only.
- **Stages (`current_state`):** QUEUED → DISCOVER → MAP_FORM → EXTRACT_METADATA → CHECK_DUPLICATE → MAP_ANSWERS → GENERATE_COVER_LETTER (only if a field is mapped to the cover letter, `llm.cover_letter.cover_letter_requested`) → FILL, then by mode. The posting's location (`discovered.job_location or metadata.location`) is passed to every answer mapping as `job_location` ([llm](llm.md)):
  - `testing`: `COMPLETED` after FILL. Nothing is submitted.
  - `review` (and any other value: fail closed): REVIEW, i.e. `AWAITING_REVIEW` with `review_round` and `review_deadline`, until the user decides or the window elapses:
    - **Approve** (signal `approve_submission(review_round)`): wait for a run slot (QUEUED), FILL again with exactly the approved mappings, then SUBMIT and `COMPLETED`.
    - **A required field without an approved answer** appears on that re-fill: it is mapped (`map_new_fields_activity`, MAP_ANSWERS) and saved as the round's `new_fields`, and the run returns to REVIEW as round + 1 with a fresh deadline.
    - **Reject** (signal `reject_submission(review_round, reason)`): `CANCELLED` / `review_rejected`.
    - **Timeout:** `EXPIRED` / `review_expired`. Nothing is submitted.
- **Signals:** a signal for any round but the current one, or one arriving when no decision is awaited, is ignored and logged. The workflow marks itself awaiting *before* it writes `AWAITING_REVIEW`, so an approval sent as soon as the API sees the round is never lost.
- **Run slots:** `acquire_run_slot_activity` admits a run while fewer than `settings.max_concurrent_runs` are `RUNNING`, re-checking every `SLOT_POLL_SECONDS` (2 s). Only `RUNNING` holds a slot: a run in review holds none, and an approved run queues again as `PENDING`/QUEUED.
- **Timeouts and retries** are module-level constants:
  - `SHORT` 2 min, `MEDIUM` 10 min, `AGENTIC` 45 min;
  - `HEARTBEAT` 2.5 min, on every long activity: discover, map_form, map_answers, map_new_fields, fill, submit;
  - `SLOT_WAIT` 7 days with a 2 min heartbeat and unlimited retries;
  - `RETRY` 2 attempts for stages, `DB_RETRY` 5 for the DB writes.

  The workflow runs in Temporal's sandbox, which re-imports this module for each run, so patching the constants reaches a workflow only on a worker with `UnsandboxedWorkflowRunner` (see `test/yahbah/workflows/test_workflow_lifecycle.py`). The one test that waits for a real timeout is marked `slow`.
- **Long activities** use `cancellation_type=WAIT_CANCELLATION_COMPLETED`, so cleanup (closing the browser) runs only after a cancelled activity has really stopped.
- **Endings.** Every terminal status is written by one fenced activity:
  - `mark_run_completed_activity` for `COMPLETED`;
  - `finish_run_activity(status, failure_reason, error_message)` for the rest.

  `finish_run_activity` writes `SUBMIT_UNCERTAIN` instead of the requested status whenever `submit_attempted_at` is set. That makes the contract's invariant (attempted + unverified = `SUBMIT_UNCERTAIN`) hold on every exit path: error, cancel, timeout, restart. In detail:
  - **Activity failure:** close the browser, then `FAILED` with `failure_reason` from `failure_reason(exc)`: the `ApplicationError.type` when it is a contract reason; `timeout`, `llm_error`, `browser_error` or `internal_error` otherwise. The workflow then fails in Temporal.
  - **Cancellation** (`is_cancelled_exception`): close the browser, then `CANCELLED` / `cancelled`. The workflow ends Canceled.
  - **Duplicate:** `DUPLICATE` / `duplicate`. The workflow completes normally.
  - **Superseded** (a fenced write refused): close the browser, write nothing, fail.

  The workflow returns the final status.

## Activities (`src/yahbah/workflows/activities/`)
- **`db_ops.py`: fenced writes.** Every status or state write runs as `UPDATE … WHERE id AND temporal_workflow_id = activity.info().workflow_id AND <the caller may hold the lease> AND status NOT IN (terminal)`, and keeps the caller's lease. If no row changes, it raises a non-retryable `ApplicationError(type="superseded")`. Outside an activity (direct calls in tests and scripts) only the terminal-status fence applies.
  - **The attempt lease** (`application_run.lease_workflow_id`, `lease_activity_seq`, `lease_attempt`): `lease.RunLeaseInterceptor`, registered on the worker, calls `take_lease(run_id)` before every activity of a run executes (except `close_session_activity`). Taking it is monotonic in (activity sequence, attempt): an older attempt can't take it back, so once a retry or a later stage has started, a timed-out attempt that is still running gets `superseded` on any write and `False` from `claim_submission()`. `run_state.activity_owner()` / `lease_clauses()` / `lease_values()` implement it.
  - Activities: `acquire_run_slot_activity`, `update_run_state_activity` (current_state only), `enter_review_activity(run_id, round, deadline_iso)`, `mark_run_completed_activity`, `finish_run_activity`, `store_credentials_activity`, `persist_artifact_activity`.
  - **`check_duplicate_activity`** fills in posting metadata, then asks `run_state.possibly_applied()` about:
    - this posting, and every posting with the same canonical URL;
    - every posting with the same title + company + location, when the blocking run applied within `duplicate_window_days`.
- **`browser.py`.** Every long activity is wrapped in `@heartbeating`.
  - `discover_activity` and `map_form_activity`.
  - **`fill_activity`:**
    - Saves the round's evidence: the `submitted_values` artifact (`submitted_values_<round>_<activity seq>-<attempt>.json`, metadata `{review_round}`, rows in the §8 shape from `agent.fill.submitted_values`) and a full-page screenshot (`filled_<round>_<seq>-<attempt>`). File names are per attempt, and `persist_artifact_activity` registers an artifact only while the caller holds the run's lease, so an attempt that lost the lease can't replace its retry's evidence; `GET /runs/{id}/review` shows the latest evidence registered for the round, i.e. the lease holder's. Failure screenshots are best effort and never hide the stage's own error.
    - First fill: fields that appear are mapped from the profile, and the browser is closed afterwards.
    - Re-fill (`FillInput.refill`): `fill_form(map_new_fields=None)` with ctx.fields from the MAP_FORM schema and all approved mappings. It returns `FillOutput.unmapped_new_fields` and `FillOutput.baseline` (what fields without an approved answer showed after code's fill; shape owned by `agent/fill.py`), which the workflow passes on as `SubmitInput.baseline`: `submit_activity` sets it on its fresh `FillContext`, and a lost-browser re-fill takes its own. The browser stays open for SUBMIT unless a required one appeared.
  - **`submit_activity`:**
    - Reads `submit_attempted_at` (set means verify only). With no live browser after a claim, it fails `submit_unverified` without clicking.
    - Otherwise it calls `agent.submit.submit_and_verify` with `claim=lambda: run_state.claim_submission(run_id, workflow_id, job_posting_id)` (the click happens only on `True`), `record=` (appends each evidence dict to the `submit_evidence.jsonl` artifact as it arrives, and registers the artifact until a registration succeeds, once more when the activity ends) and `map_new_fields=None`.
    - Once the submit has been attempted, every failure is non-retryable. Failures of the pre-claim re-fill after a lost browser follow the usual per-reason rule (`_FINAL` in `browser.py`).
  - **Agent tripwire:** on `StageFailed("possibly_submitted")`, any browser activity first calls `run_state.record_possible_submission()` (it marks the run attempted only under the lease, and always claims the job), so the run ends `SUBMIT_UNCERTAIN` / `possibly_submitted`. The error is non-retryable even if recording fails, and `finish_run_activity` treats `failure_reason = possibly_submitted` as attempted on its own: it records the claim again and writes `SUBMIT_UNCERTAIN`. A cancellation that lands after an agent left the form surfaces as `possibly_submitted` too (`agent.llm.run_agent`), and so ends the same way.
  - `close_session_activity`.
- **`llm.py`:**
  - `map_answers_activity`: stores the match score through a fenced update.
  - `map_new_fields_activity`: maps the review's new required fields with the approved mappings as prior context, and writes the round's `new_fields` artifact (per-attempt file name, like the fill evidence).
  - `generate_cover_letter_activity`: the `cover_letter` artifact's metadata carries `{text}` for the review.
  - `extract_job_metadata_activity`.
- **`heartbeat.py`:** `@heartbeating` keeps `activity.heartbeat()` going from a background task every `HEARTBEAT_EVERY` (20 s) while the activity runs, because a single 900 s model call can't heartbeat itself. A dead worker is then noticed within `HEARTBEAT`, and a timed-out attempt is cancelled locally before its retry. Temporal delivers cancellations through heartbeats too.

## Browser ownership (`src/yahbah/agent/session.py`)
- `SessionRegistry` entries carry an owner `(workflow_id, activity sequence number, attempt)` taken from `activity.info()`. The workflow's activity ids are its command sequence numbers, so a later activity, or a later attempt of the same one, takes the session over. The contract says `(workflow_id, attempt)`; attempt alone can't order two different activities, hence the sequence number.
- An earlier owner gets `None` from `get()` and `SessionSuperseded` from `open()`, which the activities turn into `superseded`. It also can't `close()` the session.
- Another workflow never gets the session.
- Outside an activity (`scripts/run_pipeline.py`) there are no checks.

## Worker (`src/yahbah/worker.py`)
- **Restart policy is resume.** The worker writes no status at start or stop (`fail_incomplete_runs` is gone). An activity interrupted by a restart is retried on the next worker, within its retry policy: it re-opens the job URL and checks the form (`_session_with_form`, `form_changed` otherwise). A submit that had already claimed only verifies, or ends `SUBMIT_UNCERTAIN`.
- **`Worker` options:** `graceful_shutdown_timeout` 2 s (`GRACEFUL_SHUTDOWN`; running activities get that long, then they are cancelled and resumed by the next worker) and `max_heartbeat_throttle_interval` 10 s (`MAX_HEARTBEAT_THROTTLE`, so a cancellation reaches an activity within about 10 s).
- **Startup:**
  - connects to Temporal (`settings.temporal_host/namespace/task_queue`, default queue `job-applications`);
  - registers all activities explicitly (add new activities to that list);
  - starts `run_status_poller` and `run_alert_poller` as asyncio tasks ([gmail](gmail.md)).
- **Shutdown:** closes every browser.
- `SessionRegistry` must be imported first (it sets browser-use env vars).

## Run creation (`src/yahbah/jobs.py`)
`submit_job_for_application(job_url, temporal_client, *, origin="manual", retry_of_run_id=None)` is the one way a run is created. It is used by `POST /jobs`, retry, and scheduled promotion (the API route and the alert poller, both `origin="discovered"`). It:
1. validates the URL;
2. raises `AlreadyApplied(blocker)` when `possibly_applied_url()` blocks: any posting whose URL or canonical URL is the job URL or its canonical form (`url_utils.canonicalize_url`);
3. upserts the posting;
4. inserts the run with the effective `submission_mode`;
5. maps a violation of the one-active-run-per-posting index (`uq_application_run_active_posting`) to `RunInFlight(run_id)`;
6. starts the workflow `application-<run_id>`.

If the start fails, the row is deleted, so it never holds the posting.

## Known limits
- **Run slots are an activity that waits** (up to 7 days, holding a worker activity slot). A backlog larger than the worker's `max_concurrent_activities` (default 100) can still stall admission; the dispatcher design is roadmap decision 5 and audit Temporal F3.
- **Approve-then-reject race (accepted):** once an approval has been accepted, the run stays `AWAITING_REVIEW` until the slot activity writes `PENDING`/`RUNNING`. A reject sent in that moment is ignored (and logged), and the run goes on to submit. Cancel still stops it.
- **Workflow tests use the stub harness:** browser and LLM activities are stubs. The real agent code is tested separately (`test/yahbah/agent/`, `test/invariants/task4_*`). The C7 acceptance gate (`test/invariants/c7_acceptance/`, marker `e2e`) runs the real worker, every real activity and Chrome against a fixture ATS, but still with scripted models. **No run has yet been made against a live site** (the user must apply the migrations and run the app).
