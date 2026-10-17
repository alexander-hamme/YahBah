# Data Model, Migrations, Config

## Tables (`src/yahbah/db/models.py`, SQLAlchemy declarative; session in `db/session.py`)
`job_posting` (metadata: company, salary, technologies, ...), `application_run` (status, `current_state`, error, timestamps, `submit_attempted_at`, `email_confirmed_at`, `email_confirmed_message_id`, test-run flag, match score), `application_step`, `application_artifact`, `application_status_update` (classified emails), `scheduled_job` (alert-sourced queue), `gmail_checkpoint` (history ids per poller), `answer_cache`, `applicant_profile` (seeded by `scripts/seed_profile.py`). Credentials storage: `src/yahbah/credentials.py` (migration 0002).

## Migrations (`alembic/versions/0001`..`0017`)
Latest `0017_agentic_submit_answer_cache.py` (untracked): adds the three run columns above and `answer_cache`.
Run with `uv run alembic upgrade head`; `alembic/env.py` uses `settings.database_url`, the same URL and role as the app.
DB role model: a single role, `yahbah_app`, owns the database (and therefore `public`, Postgres 15+) and every table, so it can run DDL and no GRANTs or default privileges are needed (setup SQL in the root README). Migrations contain no GRANTs and the code assumes no role name. Do not hardcode personal role or user names in code or docs.

## Configuration
- `src/yahbah/config.py` `Settings` (pydantic-settings; env/.env): DB URL, Temporal, Ollama + model names/ctx, `max_concurrent_runs`, `agent_max_steps`, `submit_max_attempts`, `browser_headless`, `min_field_confidence`, `duplicate_window_days`, Gmail polling, `artifacts_dir`, `prompts_path`, `personal_path`.
- `config/settings.yaml`: runtime-mutable flags (testing_mode, Gmail polling toggles/auto-archive, alert sender patterns), read fresh via `get_runtime_settings()` / `set_runtime_setting()` and editable through the API.
- Gitignored: `.env`, `config/personal.yaml`, `credentials/`, `artifacts/`, `backups/`. Do not read or copy them into docs.
