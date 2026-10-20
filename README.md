# YahBah — Autonomous Job Application System

Give it a job URL; it fills out the application form, optionally submits it, and records artifacts, job metadata, and logs. It also watches Gmail for verification codes and post-submission status emails (confirmations, rejections, interview requests) and links them back to the application.

Everything runs locally: local Ollama models, a local Chrome driven by [browser-use](https://github.com/browser-use/browser-use) (pinned in `pyproject.toml`), Postgres, and Temporal. The agentic revamp is in place on branch `browser-use`; safety hardening from the 2026-10-18 audit is in progress. The wiki's [changelog](docs/wiki/changelog.md) records what changed and why.

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
mkdir -p ~/.temporal                                         # once; the CLI does not create the directory
temporal server start-dev --db-filename ~/.temporal/yahbah.db   # Temporal (UI: http://localhost:8233)
uv run uvicorn yahbah.api.main:app --reload --port 8000      # API
uv run python -m yahbah.worker                               # worker + Gmail pollers
(cd frontend && npm run dev)                                 # dashboard: http://localhost:3000 (bound to 127.0.0.1)
```

`--db-filename` keeps workflow state across Temporal restarts; without it the dev server is in-memory and queued or running workflows are lost when it stops.

**Local only, no authentication.** The API (uvicorn's default `127.0.0.1`) and the dashboard both bind loopback, and the API rejects any `Host` or `X-Forwarded-Host` other than `localhost` / `127.0.0.1`. Add authentication before exposing either beyond this machine. Read `frontend/AGENTS.md` before changing frontend code; this Next.js version has breaking changes.

There is no separate poller process: the status and job-alert pollers run inside the worker, and each does nothing (no Gmail calls) unless its flag is enabled (see Email status tracking). On worker start and stop, rows that were RUNNING are marked FAILED because browser sessions are in-process, but the Temporal workflow is not stopped: it retries the interrupted activity on the next worker, so a run shown as FAILED can still continue. The audit's resume-on-restart policy replaces this ([run-lifecycle contract](docs/wiki/contracts/run-lifecycle.md)).

### Debug one URL without Temporal

```bash
uv run python scripts/run_pipeline.py <url>
```

Runs discover → map → answers → fill without Temporal or a run record. It does not submit unless `--submit` is passed. Output goes to `artifacts/debug-<timestamp>/`. See `--help` for the other flags.

## Tests

```bash
uv run pytest                    # everything, offline (about a minute)
uv run pytest -m "not browser"   # skip tests that launch headless Chrome
uv run pytest -m "not db"        # skip tests that need the test Postgres (also "not temporal")

cd frontend
npm test                         # Vitest + Testing Library
npm run lint                     # ESLint (reports some older findings)
npx tsc --noEmit                 # type check
```

Tests live in `test/`; the shared harness is `test/harness/` and the safety-invariant suite `test/invariants/` (mostly strict `xfail` until the batch-1 work lands). Markers:
- `browser`: headless Chrome against local fixture pages (`test/yahbah/fixtures/`), no network.
- `db`: a disposable Postgres database created and dropped per session through `TEST_DATABASE_URL` (default `postgresql+psycopg://yahbah_test@localhost/postgres`). Create that role once, with passwordless local access: `CREATE ROLE yahbah_test WITH LOGIN CREATEDB;`. The tests skip if Postgres is unreachable.
- `temporal`: workflows on a local Temporal dev server (the `temporal` binary on PATH).
- `live_llm`: the real local models; skipped unless selected with `-m live_llm` or `YAHBAH_LIVE_LLM=1`.

Tests cannot reach the real database, the network beyond localhost, or Ollama (outside `live_llm`). `scripts/save_fixture.py <url> <name>` saves a live page as a new fixture. `scripts/manual/` holds hand-run live checks (Gmail, metadata extraction), not part of pytest. Details: [docs/wiki/development.md](docs/wiki/development.md).

## Usage

```bash
# Submit an application run
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_url": "https://boards.greenhouse.io/company/jobs/12345"}'

curl http://localhost:8000/runs/<run_id>             # status
curl http://localhost:8000/runs/<run_id>/artifacts   # artifacts
```

Job URLs must be public `http(s)` URLs. `POST /jobs` and `POST /scheduled` answer 422 for other schemes, `localhost`, private or link-local addresses, and hosts that do not resolve, so submitting a job needs working DNS. To exercise a local page, use `scripts/run_pipeline.py`, which skips this check.

Greenhouse has a dedicated board profile; other sites go through the generic profile (`src/yahbah/agent/boards.py`). Success on arbitrary sites varies.

Files for each run are stored under `ARTIFACTS_DIR/<run_id>/`: screenshots, form schema, field mappings, submitted values, cover letter, confirmation text and HTML, `trace.jsonl` and the `llm/` call logs. The artifacts endpoint lists only files recorded in the database; `trace.jsonl` and the `llm/` logs are not recorded, so they exist only on disk. The exact set depends on the run. LLM call logs are described in [docs/wiki/modules/llm.md](docs/wiki/modules/llm.md).

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
- The job-alert poller reads alert emails (LinkedIn, Indeed, etc., by sender pattern), scores jobs, queues them in `scheduled_job`, and promotes them to runs after a delay. Only a job that was actually scored, at or above `job_alert_min_match_score`, is queued; one that could not be scored is skipped. At most `job_alert_max_promotions_per_cycle` (default 5) jobs are promoted per cycle, alerts older than `job_alert_max_age_days` (default 14) are ignored, and queued jobs older than that expire. Alert links and job pages are fetched only from public hosts (the same URL policy as above).
- Polling loops are off by default and toggled at runtime in `config/settings.yaml` (`gmail_status_polling_enabled`, `job_alert_polling_enabled`, auto-archive toggles, `testing_mode`), via `PUT /settings/{key}`. A poller does nothing when its flag is not exactly `true`, including the first cycle at worker startup (it makes no Gmail calls, label changes or promotions). The alert limits above default from `config.py` and can be overridden in `config/settings.yaml`.
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
test/             # pytest suite (harness/, invariants/)   docs/wiki/ # architecture, module docs, changelog
```
