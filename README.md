# YahBah — Autonomous Job Application System

Give it a job URL; it fills out the application form, optionally submits it, and records artifacts, job metadata, and logs. It also watches Gmail for verification codes and post-submission status emails (confirmations, rejections, interview requests) and links them back to the application.

Everything runs locally: local Ollama models, a local Chrome driven by [browser-use](https://github.com/browser-use/browser-use) (pinned in `pyproject.toml`), Postgres, and Temporal. The branch `browser-use` is mid-way through an "agentic revamp"; see the wiki for what is in flux.

## Architecture

```
 Next.js frontend ──► FastAPI API ──► Temporal ──► Worker
   (frontend/)      (yahbah.api)     (workflow)    (yahbah.worker)
                                                     │
              ┌──────────────────────┬───────────────┼─────────────────┐
              ▼                      ▼               ▼                 ▼
      Browser activities       LLM activities   DB activities    Gmail pollers
      (yahbah.agent,           (yahbah.llm,     (Postgres via    (status + job
       browser-use/Chrome)      Ollama)          SQLAlchemy)      alerts)
```

`POST /jobs` starts an `ApplicationWorkflow` (`src/yahbah/workflows/application.py`) that moves through these stages:

```
QUEUED → DISCOVER → MAP_FORM → EXTRACT_METADATA → CHECK_DUPLICATE
  → MAP_ANSWERS → GENERATE_COVER_LETTER → FILL → SUBMIT → DONE
```

Each stage is a Temporal activity with its own retries and timeouts. With the runtime setting `testing_mode` on, a run stops after FILL and does not submit.

Design rule: **code first, models as fallbacks**. Each browser stage tries deterministic code, then one structured model call, then an agent loop, and verifies the result in code at each tier. Agents are never given a submit tool; submission is gated code (`src/yahbah/agent/submit.py`). Board-specific logic lives in `src/yahbah/agent/boards.py`.

The worker process also runs two background Gmail pollers (application status emails, job-alert emails). Each application uses a unique per-run email alias (`you+job-xxxx@gmail.com`, `src/yahbah/credentials.py`) so replies can be matched to it.

For module-level detail, start at [docs/wiki/index.md](docs/wiki/index.md).

## Prerequisites

| Tool | Notes |
|---|---|
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | `brew install python@3.12 uv` |
| PostgreSQL | `brew install postgresql@15` (version not pinned by the code) |
| Temporal CLI | `brew install temporal` (dev server) |
| Ollama | `brew install ollama` |
| Chrome/Chromium | launched locally by browser-use (headless unless `BROWSER_HEADLESS=false`); see setup step 1 |
| Node.js + npm | frontend only |

The default Ollama models are large: `config.py` notes that the two default models stay loaded together (~71 GB). Pick smaller models via env vars if your machine needs it.

## Setup

### 1. Install dependencies

```bash
uv sync
(cd frontend && npm install)
uvx playwright install chromium   # optional, see below
```

browser-use drives Chrome over CDP and does not use the `playwright` Python package. It uses an installed Google Chrome or Chromium, or the Playwright Chromium cache (`~/Library/Caches/ms-playwright/chromium-*`, or `PLAYWRIGHT_BROWSERS_PATH`). If it finds none, it runs `uvx playwright install chromium` on first launch, so pre-installing just avoids that delay.

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`. Every setting, its default, and the env var it maps to (the upper-cased field name) is defined in [`src/yahbah/config.py`](src/yahbah/config.py), which is the authority. Notable ones: `DATABASE_URL`, `TEMPORAL_HOST`, `OLLAMA_BASE_URL`, `ARTIFACTS_DIR`, `MAX_CONCURRENT_RUNS`, `BROWSER_HEADLESS`, `MIN_FIELD_CONFIDENCE`, `DUPLICATE_WINDOW_DAYS`, Gmail settings.

Ollama models are chosen by two roles, not by one model name: `MODEL_DRIVER` / `CTX_DRIVER` (agent loops, DOM/HTML work, JSON mapping) and `MODEL_PROSE_VISION` / `CTX_PROSE_VISION` (cover letters, free-text answers, screenshots). Defaults are in `config.py`.

Personal data goes in `config/personal.yaml` (gitignored; copy `config/personal.example.yaml`). It holds your applicant `profile`, `known_answers`, and the cover-letter prompt, and is merged with `config/prompts.yaml` at load time. Never commit it or put secrets in docs.

### 3. Pull the Ollama models

```bash
ollama pull <model>   # for each of the two models configured in src/yahbah/config.py / your .env
```

### 4. Create the database and run migrations

As a Postgres superuser, create the app role and a database it owns:

```sql
CREATE ROLE yahbah_app WITH LOGIN PASSWORD 'change-me';
CREATE DATABASE yahbah OWNER yahbah_app;
```

Point `DATABASE_URL` in `.env` at it (`postgresql+psycopg://yahbah_app:change-me@localhost/yahbah`), then migrate:

```bash
uv run alembic upgrade head
```

The app and alembic use the same role: `alembic/env.py` reads `settings.database_url`. The role owns the database, and therefore the `public` schema (Postgres 15+), plus every table that migrations create, so no GRANTs are needed.

### 5. Seed your applicant profile

```bash
uv run python scripts/seed_profile.py
```

Reads the `profile` key of `config/personal.yaml` and inserts or updates the applicant profile row.

### 6. Gmail integration

Needed for automatic entry of email verification codes and for status/alert tracking.

```bash
# Save the OAuth2 client secret from Google Cloud Console as:
#   ~/.config/yahbah/gmail/credentials.json      (GMAIL_CREDENTIALS_DIR)
uv run python -m yahbah.gmail.client             # one-time OAuth flow in the browser
```

Requires a Google Cloud project with the Gmail API enabled and an OAuth consent screen configured.

## Running

Four processes (the frontend is optional):

```bash
temporal server start-dev                                    # Temporal (UI: http://localhost:8233)
uv run uvicorn yahbah.api.main:app --reload --port 8000      # API
uv run python -m yahbah.worker                               # worker + Gmail pollers
(cd frontend && npm run dev)                                 # frontend: http://localhost:3000
```

The API only allows CORS from `http://localhost:3000`. Read `frontend/AGENTS.md` before changing frontend code; this Next.js version has breaking changes.

There is no separate poller process: the status and job-alert pollers run inside the worker. A worker restart marks runs that were RUNNING as FAILED, because browser sessions are in-process.

### Debug one URL without Temporal

```bash
uv run python scripts/run_pipeline.py <url>
```

Runs discover → map → answers → fill without Temporal or a run record. It does not submit unless `--submit` is passed. Output goes to `artifacts/debug-<timestamp>/`. See `--help` for the other flags.

## Tests

```bash
uv run pytest                    # everything
uv run pytest -m "not browser"   # skip tests that launch headless Chrome
```

Tests live in `test/`. The `browser` marker covers tests that drive Chrome against offline HTML fixtures (`test/yahbah/fixtures/`); they need no network. `scripts/save_fixture.py <url> <name>` saves a live page as a new fixture. `scripts/manual/` holds hand-run live checks (Gmail, metadata extraction), not part of pytest.

## Usage

```bash
# Submit an application run
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_url": "https://boards.greenhouse.io/company/jobs/12345"}'

curl http://localhost:8000/runs/<run_id>             # status
curl http://localhost:8000/runs/<run_id>/artifacts   # artifacts
```

Greenhouse has a dedicated board profile; other sites go through the generic profile (`src/yahbah/agent/boards.py`). Success on arbitrary sites varies.

Artifacts for each run are stored under `ARTIFACTS_DIR/<run_id>/` (screenshots, form schema, submitted values, cover letter, `trace.jsonl`, LLM logs). The exact set depends on the run; list them via the artifacts endpoint. LLM call logs are described in [docs/wiki/modules/llm.md](docs/wiki/modules/llm.md).

Job metadata is queryable in Postgres:

```sql
SELECT title, company, salary_min, salary_max FROM job_posting
WHERE salary_min IS NOT NULL ORDER BY salary_max DESC;

SELECT title, company FROM job_posting
WHERE technologies @> '["PyTorch"]'::jsonb;
```

## Email status tracking

- Each application uses a unique email alias (`user+job-<hash>@gmail.com`); the hash comes from the ATS, company, and job ID and extends in length if a collision occurs.
- The status poller (in the worker) reads Gmail history incrementally, keeps messages sent to a `+alias`, classifies them with the LLM (UNDER_REVIEW, ONLINE_ASSESSMENT, INTERVIEW_REQUEST, INTERVIEW_SCHEDULED, OFFER, REJECTED, WITHDRAWN, OTHER), links them to the run, and optionally archives them into a Gmail folder per status. A history checkpoint is kept in the database.
- The job-alert poller reads alert emails (LinkedIn, Indeed, etc., by sender pattern), scores jobs, queues them in `scheduled_job`, and promotes them to runs after a delay.
- Polling loops are off by default and toggled at runtime in `config/settings.yaml` (`gmail_status_polling_enabled`, `job_alert_polling_enabled`, auto-archive toggles, `testing_mode`), via `PUT /settings/{key}`. Static defaults are in `config.py`.
- Trigger a cycle manually: `POST /gmail/poll` (status) or `POST /gmail/alert-poll` (alerts).

## API overview

| Prefix | Endpoints |
|---|---|
| `/jobs` | `POST` enqueue a run |
| `/runs` | `GET /{id}`, `/{id}/steps`, `/{id}/artifacts`, `/{id}/status-updates`, artifact download; `POST /{id}/cancel`, `/{id}/retry`, `/bulk-delete`; `DELETE /{id}` |
| `/applications` | `GET` list, `GET /stats` |
| `/scheduled` | create, list, stats, get, `approve` / `reject` / `hold` / `promote`, delete |
| `/settings` | `GET`; `PUT /{key}` (body `{"value": ...}`) |
| `/gmail` | `POST /poll`, `POST /alert-poll` |
| `/health` | `GET` |

FastAPI serves interactive docs at `http://localhost:8000/docs`.

## Project structure

```
src/yahbah/
├── agent/        # browser-use automation: discover, fill, submit, boards, session, tools
├── api/          # FastAPI app (main.py) + routes/ (jobs, runs, applications, scheduled)
├── db/           # SQLAlchemy models + async session
├── gmail/        # client (OAuth), parser, status poller, job-alert poller/parser
├── llm/          # Ollama client, model roles, field mapper, cover letter, LLM logs
├── workflows/    # ApplicationWorkflow + activities/ (browser, db_ops, llm)
├── config.py     # Settings (env/.env), runtime settings, prompts loading
├── schemas.py    # Temporal-serializable dataclasses
├── credentials.py, url_utils.py, jobs.py
└── worker.py     # worker entrypoint + pollers
alembic/          # migrations      config/   # prompts, settings, personal (gitignored)
frontend/         # Next.js UI      scripts/  # run_pipeline, seed_profile, save_fixture, manual/
test/             # pytest suite    docs/wiki/ # architecture and module docs
```
