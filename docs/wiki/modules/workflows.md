# Workflows, Activities, Worker

## Workflow
`ApplicationWorkflow` in `src/yahbah/workflows/application.py`, one per `application_run`. Input: `ApplicationWorkflowInput(run_id, job_url)`. Stage order in [architecture](../architecture.md). Timeouts: `short` 2 min, `medium` 10 min, `agentic` 45 min (browser stages and MAP_ANSWERS); retry policy `maximum_attempts=2`. Run-slot wait: 7-day timeout, 2-min heartbeat, no retry cap. Each stage calls `update_run_state_activity`.

Failure: any `ActivityError` -> `mark_run_failed_activity` + `close_session_activity`, then re-raise. Duplicate job -> `mark_run_duplicate_activity`, close session, then `raise asyncio.CancelledError(...)`. The SDK turns a `CancelledError` into a workflow cancellation only when cancellation was requested (`_cancel_requested` in `temporalio/worker/_workflow_instance.py`, SDK 1.20.0); otherwise it fails the workflow. So Temporal records a duplicate as a **Failed** workflow while the DB row is `DUPLICATE`. Activity inputs/outputs are dataclasses in `src/yahbah/schemas.py`.

The agentic activities set only `start_to_close_timeout`; no `heartbeat_timeout` is set except on the run-slot wait. Batch 1 (cancel, retry, review gate, heartbeats) changes this workflow's shape; see [contracts/run-lifecycle.md](../contracts/run-lifecycle.md).

## Activities (`src/yahbah/workflows/activities/`)
- `browser.py`: `discover_activity`, `map_form_activity`, `fill_activity`, `submit_activity`, `close_session_activity`. Thin wrappers over `yahbah.agent`; take screenshots/artifacts; re-open the job URL if the session is gone (e.g. worker restart). `submit_activity` writes `submit_attempted_at` via `db_ops.mark_submit_attempted` before clicking.
- `llm.py`: `map_answers_activity`, `generate_cover_letter_activity`, `extract_job_metadata_activity`.
- `db_ops.py`: `acquire_run_slot_activity` (honours `settings.max_concurrent_runs`, default 1), `update_run_state_activity`, `mark_run_completed/failed/duplicate_activity`, `check_duplicate_activity` (another `COMPLETED` run on the same canonical URL at any time, or on the same title+company+location within `duplicate_window_days`; a COMPLETED test-mode run counts, a FAILED run (including an uncertain submit) does not; batch 1 replaces this with `possibly_applied()`, [contract §5](../contracts/run-lifecycle.md)), `persist_artifact_activity`, `store_credentials_activity`.

## Worker (`src/yahbah/worker.py`)
`fail_incomplete_runs` runs on startup and again in the shutdown `finally`: it marks every `RUNNING` row FAILED ("Worker restarted" / "Worker shutting down"; the browser session is gone). It edits the DB only, never the Temporal workflow, and leaves `PENDING` rows alone. **Current behaviour after a restart** (audit probe B, [temporal-platform F1](../../audits/2026-10-18/temporal-platform.md)):
- Temporal keeps the workflow alive and retries the interrupted activity on the next worker, within its 2-attempt retry policy. The activity re-opens the job URL and, given a mapped schema, checks the form is unchanged (`_session_with_form`).
- The next `update_run_state_activity` sets the row back to `RUNNING`, so a run shown as FAILED can continue and even submit.
- Run slots are counted from `RUNNING` rows, so the FAILED mark frees the slot and a queued run can start alongside it; `max_concurrent_runs` can be exceeded.
- After a hard kill, the dead activity is noticed only at its start-to-close timeout (45 min for the agentic stages).

Batch 1 changes this to a pure resume policy: no status flip at worker start or stop, heartbeat timeouts, and fenced status writes ([contract §7](../contracts/run-lifecycle.md)).

Startup then connects to Temporal (`settings.temporal_host/namespace/task_queue`, default queue `job-applications`), registers all activities explicitly (add new activities to that list), and starts `run_status_poller` and `run_alert_poller` as asyncio tasks ([gmail](gmail.md)). `SessionRegistry` must be imported first (sets browser-use env vars).
