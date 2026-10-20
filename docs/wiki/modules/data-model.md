# Data Model, Migrations, Config

## Tables (`src/yahbah/db/models.py`, SQLAlchemy declarative; session in `db/session.py`)
`job_posting` (metadata: company, salary, technologies, ...), `application_run` (status, `current_state`, error, timestamps, `submit_attempted_at`, `email_confirmed_at`, `email_confirmed_message_id`, test-run flag, match score), `application_step` (the API reads it, but no code writes rows), `application_artifact`, `application_status_update` (classified emails), `scheduled_job` (alert-sourced queue), `gmail_checkpoint` (history ids per poller), `answer_cache`, `applicant_profile` (seeded by `scripts/seed_profile.py`). Credentials storage: `src/yahbah/credentials.py` (migration 0002).

## Migrations (`alembic/versions/0001`..`0017`)
Latest `0017_agentic_submit_answer_cache.py` (committed in `999f57a`; the alembic head, and applied to the dev DB): adds `application_run.submit_attempted_at`, `email_confirmed_at`, `email_confirmed_message_id` and the `answer_cache` table.
Planned (batch 1, not yet in the tree): `0018_run_lifecycle` (new status/`failure_reason`/`submission_mode`/`origin`/review columns, `submission_claim` table, drops `is_test_run`) and `0019_answer_cache_provenance`; see [contract §9](../contracts/run-lifecycle.md).
Run with `uv run alembic upgrade head`; `alembic/env.py` uses `settings.database_url`, the same URL and role as the app.
DB role model: a single role, `yahbah_app`, owns the database (and therefore `public`, Postgres 15+) and every table, so it can run DDL and no GRANTs or default privileges are needed (setup SQL in the root README). Migrations contain no GRANTs and the code assumes no role name. Do not hardcode personal role or user names in code or docs.

## Configuration
- `src/yahbah/config.py` `Settings` (pydantic-settings; env/.env): DB URL, Temporal, Ollama + model names/ctx, `max_concurrent_runs`, `submit_max_attempts`, `browser_headless`, `min_field_confidence`, `duplicate_window_days`, Gmail polling, `artifacts_dir`, `prompts_path`, `personal_path`. `agent_max_steps` is defined but never read: each `run_agent` call hard-codes its own step limit ([agent](agent.md)). Job-alert admission bounds: `job_alert_max_promotions_per_cycle` (default 5) and `job_alert_max_age_days` (default 14); like the other alert keys they can be overridden in `config/settings.yaml` ([gmail](gmail.md)).
- `config/settings.yaml`: runtime-mutable flags (testing_mode, which batch 1 replaces with `submission_mode` per [contract §3](../contracts/run-lifecycle.md); Gmail polling toggles/auto-archive, alert sender patterns), read fresh via `get_runtime_settings()` / `set_runtime_setting()` and editable through the API.
- Gitignored: `.env`, `config/personal.yaml`, `credentials/`, `artifacts/`, `backups/`. Do not read or copy them into docs.
