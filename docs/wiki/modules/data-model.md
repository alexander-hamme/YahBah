# Data Model, Migrations, Config

## Tables (`src/yahbah/db/models.py`, SQLAlchemy declarative; session in `db/session.py`)
- `job_posting`: metadata (company, salary, technologies, `canonical_url`, ...).
- `application_run`:
  - status ([contract §1](../contracts/run-lifecycle.md); vocabularies in `run_state.py`), `current_state` (stage), `failure_reason`, `error_message`, timestamps, match score;
  - `submission_mode` (snapshot at creation; `NULL` = a pre-contract run), `origin` (`manual | discovered`), `review_round`, `review_deadline`, `retry_of_run_id` (`ON DELETE SET NULL`);
  - `submit_attempted_at` (written with the submission claim, or by the agent tripwire), `email_confirmed_at`, `email_confirmed_message_id`;
  - the attempt lease `lease_workflow_id`, `lease_activity_seq`, `lease_attempt`: the activity attempt acting on the run now, taken at every activity start and required by fenced writes and the claim ([workflows](workflows.md)).
  - **At most one active run per posting:** a partial unique index `uq_application_run_active_posting` on `job_posting_id` `WHERE status IN ('PENDING','RUNNING','AWAITING_REVIEW')`.
  - **Status writes:** only the run's own workflow writes status, through fenced activities ([workflows](workflows.md)).
- `submission_claim`: `posting_key` (batch 1: `str(job_posting_id)`) PK, `run_id` (`ON DELETE SET NULL`), `claimed_at`. Taken atomically with `submit_attempted_at` by `run_state.claim_submission()`. It outlives its run and is released only by confirm-not-submitted ([contract §6](../contracts/run-lifecycle.md)).
- `application_step`: the API reads it, but no code writes rows.
- `application_artifact`: files per run. The review evidence is tagged `artifact_metadata.review_round` ([workflows](workflows.md)).
- `application_status_update`: classified emails.
- `scheduled_job`: the alert-sourced queue. `promoted_run_id` is `ON DELETE SET NULL`.
- `gmail_checkpoint`: history ids per poller.
- `answer_cache`.
- `applicant_profile`: seeded by `scripts/seed_profile.py`.

Credentials storage: `src/yahbah/credentials.py` (migration 0002).

## Migrations (`alembic/versions/`)
- `0017_agentic_submit_answer_cache.py` (committed in `999f57a`, and applied to the dev DB): adds `application_run.submit_attempted_at`, `email_confirmed_at`, `email_confirmed_message_id` and the `answer_cache` table.
- `0018_run_lifecycle.py` (batch 1, task 3; in the working tree, not yet applied to the dev DB; [contract §9](../contracts/run-lifecycle.md)):
  - **Drift repair:** `job_posting.canonical_url`, `location` and `ix_job_posting_canonical_url` are added only if missing. The ORM always had them, but no migration created them. Downgrade leaves them.
  - **New columns:** the `application_run` columns above, including the lease columns. Drops `is_test_run`.
  - **Backfill:**
    - `submission_mode='testing'` where `is_test_run`;
    - `origin` `discovered` for promoted runs, else `manual`;
    - `FAILED` with `submit_attempted_at` → `SUBMIT_UNCERTAIN` / `submit_unverified`;
    - `FAILED` "Cancelled by user" → `CANCELLED` / `cancelled`;
    - `DUPLICATE` → `failure_reason` `duplicate`;
    - other `FAILED` rows get the `failure_reason` that prefixes their `error_message`, when it is a contract value.
  - **Orphans:** older duplicate active runs of one posting become `FAILED` / `superseded` (or `SUBMIT_UNCERTAIN` if attempted) so the unique index can be created. They are orphans of the in-memory Temporal dev server.
  - **Claims:** creates `submission_claim` with one claim per posting whose run had `submit_attempted_at`.
  - **Downgrade:** restores `is_test_run` and maps the new statuses back to `FAILED`.
- `0019_answer_cache_provenance.py` (batch 1, task 5; in the working tree, not yet applied to the dev DB): deletes every `answer_cache` row (none has provenance), makes the primary key `(namespace, question_key)` and adds `namespace`, `basis`, `basis_hash`, `model`, `prompt_hash` and `run_id` (`ON DELETE SET NULL`). Downgrade drops the debug-namespace rows and restores the old key; the deleted rows are not restored ([llm](llm.md)).
Run with `uv run alembic upgrade head`; `alembic/env.py` uses `settings.database_url`, the same URL and role as the app.
DB role model: a single role, `yahbah_app`, owns the database (and therefore `public`, Postgres 15+) and every table, so it can run DDL and no GRANTs or default privileges are needed (setup SQL in the root README). Migrations contain no GRANTs and the code assumes no role name. Do not hardcode personal role or user names in code or docs.

## Configuration
- `src/yahbah/config.py` `Settings` (pydantic-settings; env/.env): DB URL, Temporal, Ollama + model names/ctx, `max_concurrent_runs`, `submit_max_attempts`, `browser_headless`, `min_field_confidence`, `duplicate_window_days`, Gmail polling, `artifacts_dir`, `prompts_path`, `personal_path`. `agent_max_steps` is defined but never read: each `run_agent` call hard-codes its own step limit ([agent](agent.md)). Job-alert admission bounds: `job_alert_max_promotions_per_cycle` (default 5) and `job_alert_max_age_days` (default 14); like the other alert keys they can be overridden in `config/settings.yaml` ([gmail](gmail.md)).
- `config/settings.yaml`: runtime-mutable settings, read fresh via `get_runtime_settings()` / `set_runtime_setting()` and editable through the API:
  - `submission_mode` (`testing | review`; [contract §3](../contracts/run-lifecycle.md)) and `review_window_hours` (float hours);
  - Gmail polling toggles and auto-archive, alert sender patterns, and the alert admission bounds.

  `config.RUNTIME_SETTINGS` lists every writable key with its validator. `validate_runtime_setting(key, value)` returns the value to store, or raises `ValueError` for an unknown key or invalid value. Runs are created from `get_effective_runtime_settings()`: a missing or invalid `submission_mode` means `review` (fail closed), and a missing or invalid window means 48 h. The old `testing_mode` key is no longer read; a stale copy in a settings file is ignored.
- Gitignored: `.env`, `config/personal.yaml`, `credentials/`, `artifacts/`, `backups/`. Do not read or copy them into docs.
