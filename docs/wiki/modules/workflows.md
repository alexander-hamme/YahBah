# Workflows, Activities, Worker

## Workflow
`ApplicationWorkflow` in `src/yahbah/workflows/application.py`, one per `application_run`. Input: `ApplicationWorkflowInput(run_id, job_url)`. Stage order in [architecture](../architecture.md). Timeouts: `short` 2 min, `medium` 10 min, `agentic` 45 min (browser stages and MAP_ANSWERS); retry policy `maximum_attempts=2`. Run-slot wait: 7-day timeout, 2-min heartbeat, no retry cap. Each stage calls `update_run_state_activity`.

Failure: any `ActivityError` -> `mark_run_failed_activity` + `close_session_activity`, then re-raise. Duplicate job -> `mark_run_duplicate_activity`, close session, raise `CancelledError`. Activity inputs/outputs are dataclasses in `src/yahbah/schemas.py`.

## Activities (`src/yahbah/workflows/activities/`)
- `browser.py`: `discover_activity`, `map_form_activity`, `fill_activity`, `submit_activity`, `close_session_activity`. Thin wrappers over `yahbah.agent`; take screenshots/artifacts; re-open the job URL if the session is gone (e.g. worker restart). `submit_activity` writes `submit_attempted_at` via `db_ops.mark_submit_attempted` before clicking.
- `llm.py`: `map_answers_activity`, `generate_cover_letter_activity`, `extract_job_metadata_activity`.
- `db_ops.py`: `acquire_run_slot_activity` (honours `settings.max_concurrent_runs`, default 1), `update_run_state_activity`, `mark_run_completed/failed/duplicate_activity`, `check_duplicate_activity` (canonical URL, or title+company+location within `duplicate_window_days`), `persist_artifact_activity`, `store_credentials_activity`.

## Worker (`src/yahbah/worker.py`)
Startup: `fail_incomplete_runs` marks `RUNNING` runs FAILED (browser state is lost; `PENDING` runs are left for Temporal to resume), connects to Temporal (`settings.temporal_host/namespace/task_queue`, default queue `job-applications`), registers all activities explicitly (add new activities to that list), and starts `run_status_poller` and `run_alert_poller` as asyncio tasks ([gmail](gmail.md)). `SessionRegistry` must be imported first (sets browser-use env vars).
