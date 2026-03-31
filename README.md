# YahBah — Autonomous Job Application System

End-to-end system that takes a job URL, autonomously fills and submits the application, and stores all artifacts, metadata, and logs. Fully hands-off from URL to confirmation.

## Architecture

```
                         ┌──────────────────────────┐
                         │   FastAPI Control Plane   │
                         │   POST /jobs → enqueue    │
                         │   GET  /runs → status     │
                         └────────────┬─────────────┘
                                      │
                              Temporal Workflow
                         (durable, retryable steps)
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         │                            │                            │
   Browser Activities          LLM Activities              DB Activities
   (Playwright)                (Ollama)                    (Postgres)
         │                            │                            │
   ┌─────┴──────┐          ┌─────────┴──────────┐        ┌───────┴────────┐
   │ Auth walls  │          │ Field mapping      │        │ Run state      │
   │ Form extract│          │ Cover letter gen   │        │ Artifacts      │
   │ Form fill   │          │ Metadata extraction│        │ Job metadata   │
   │ Submit      │          │ Select option pick │        │ Dedup check    │
   │ Verify code │          └────────────────────┘        │ Credentials    │
   └─────┬───────┘                                        └────────────────┘
         │
   Gmail Ingestion
   (verification codes)
```

### Workflow State Machine

```
AUTH_CHECK → EXTRACT_FORM → EXTRACT_METADATA → CHECK_DUPLICATE
  → MAP_FIELDS → GENERATE_COVER_LETTER → FILL_AND_SUBMIT → DONE
```

Each step is a Temporal activity with independent retry policies and timeouts. If the worker crashes mid-fill, Temporal resumes from the last completed step — no work is lost.

## Tech Stack

| Layer | Technology | Role |
|---|---|---|
| **Orchestration** | Temporal | Durable workflow execution, automatic retries, failure recovery |
| **API** | FastAPI | Control plane — enqueue jobs, query run status |
| **Browser** | Playwright (Chromium) | Headless form extraction, filling, file upload, submission |
| **LLM** | Ollama (local) | Field mapping, cover letter generation, metadata extraction, dropdown selection |
| **Database** | PostgreSQL + SQLAlchemy | System of record — runs, artifacts, job metadata, applicant profile |
| **Email** | Gmail API | Verification code retrieval via incremental polling |
| **Deps** | uv | Fast Python package management |
| **Migrations** | Alembic | Schema versioning |

### Key Design Decisions

- **Async-first**: all I/O is async (`asyncio`, `psycopg[asyncio]`, `httpx`). Blocking Gmail API calls are wrapped with `asyncio.to_thread()`.
- **Failure-persistent**: Temporal guarantees exactly-once execution per activity. A crashed worker resumes from the last checkpoint, not from scratch.
- **Deterministic filling**: form fields are mapped by the LLM once, then filled mechanically — no LLM in the hot path of typing into inputs.
- **Confidence gating**: LLM field mappings below a configurable threshold (default 0.7) are skipped for optional fields, attempted with a warning for required fields.
- **Known-answer fallback**: ~20 sensitive/policy questions (salary expectation, work authorization, EEO demographics) use hardcoded answers — never sent to the LLM.
- **Embedded form support**: company career pages that embed Greenhouse via iframe (e.g. Airbnb) are detected and handled transparently — the system clicks the Application tab, switches to the iframe context, and operates within it.

## Capabilities

- **Account creation**: detects Greenhouse auth walls, generates unique email aliases + strong passwords, creates accounts automatically
- **Form extraction**: reads all inputs, selects, textareas, checkboxes from the DOM; resolves labels via `<label for>`, ancestors, placeholders
- **Smart filling**: LLM maps form fields to profile columns; native `<select>` and custom React dropdowns are both handled (click → collect options → LLM picks best match)
- **File upload**: resume and cover letter PDF upload via file-chooser interception (works with Greenhouse's React uploader)
- **Cover letter generation**: LLM generates a tailored cover letter per job, rendered as PDF with custom typography
- **Email verification**: when Greenhouse requires a security code, polls Gmail API for the code email, extracts the code (heuristic + LLM fallback), and types it into the verification inputs
- **Job metadata extraction**: LLM extracts title, company, location, salary range, technology stack, and a 40-word description summary; stored in Postgres for querying
- **Duplicate detection**: blocks re-application to the same job by canonical URL or (title + company + location) within a configurable window
- **Artifact capture**: screenshots at every stage, Playwright traces, form schemas, field mappings, cover letters, confirmation text

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.12+ | `brew install python@3.12` |
| uv | latest | `brew install uv` |
| PostgreSQL | 15+ | `brew install postgresql@15` |
| Temporal | latest | `brew install temporal` |
| Ollama | latest | `brew install ollama` |

## Setup

### 1. Install dependencies

```bash
uv sync
uv run playwright install chromium
```

### 2. Configure environment

Copy `.env.example` to `.env` and edit:

```
DATABASE_URL=postgresql+psycopg://yahbah_app:yahbah_app@localhost/yahbah
TEMPORAL_HOST=localhost:7233
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3:70b
ARTIFACTS_DIR=./artifacts
```

### 3. Create the database and run migrations

```bash
createdb yahbah
uv run alembic upgrade head
```

### 4. Set up database roles

```sql
-- Create restricted app role (no TRUNCATE, no DROP)
CREATE ROLE yahbah_app WITH LOGIN PASSWORD 'yahbah_app';
GRANT CONNECT ON DATABASE yahbah TO yahbah_app;
GRANT USAGE ON SCHEMA public TO yahbah_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO yahbah_app;
ALTER DEFAULT PRIVILEGES FOR ROLE alex IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO yahbah_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO yahbah_app;
ALTER DEFAULT PRIVILEGES FOR ROLE alex IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO yahbah_app;
```

Run migrations as superuser: `DATABASE_URL=postgresql+psycopg://localhost/yahbah uv run alembic upgrade head`

### 5. Seed your applicant profile

```bash
uv run python scripts/seed_profile.py
```

### 6. Pull the LLM model

```bash
ollama pull llama3:70b
```

### 7. Gmail integration (optional — required for email verification)

```bash
# Place OAuth2 client secret from Google Cloud Console
mkdir -p ~/.config/yahbah/gmail
# Save credentials.json to ~/.config/yahbah/gmail/credentials.json

# Complete one-time OAuth flow
uv run python -m yahbah.gmail.client
```

Requires: Google Cloud project with Gmail API enabled, OAuth consent screen configured.

---

## Running

Start all three processes:

```bash
# Terminal 1 — Temporal dev server
temporal server start-dev

# Terminal 2 — API
uv run uvicorn yahbah.api.main:app --reload --port 8000

# Terminal 3 — Worker
uv run python -m yahbah.worker
```

---

## Usage

### Submit a job application

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_url": "https://boards.greenhouse.io/company/jobs/12345"}'
```

Supports both direct Greenhouse URLs and embedded career pages (e.g. `https://careers.airbnb.com/positions/7175614/`).

### Check run status

```bash
curl http://localhost:8000/runs/<run_id>
```

### List artifacts

```bash
curl http://localhost:8000/runs/<run_id>/artifacts
```

### Artifacts

| File | Description |
|---|---|
| `screenshot_after_extract.png` | Form after extraction |
| `screenshot_before_fill.png` | Form before filling |
| `screenshot_before_submit.png` | Filled form, pre-submit |
| `screenshot_after_verification.png` | After entering email verification code |
| `screenshot_confirmation.png` | Confirmation page |
| `screenshot_submit_failed.png` | Page state on submission failure |
| `form_schema.json` | Extracted form fields |
| `field_mappings.json` | LLM field mapping result |
| `cover_letter.pdf` | Generated cover letter |
| `confirmation.txt` | Confirmation page text |
| `trace.zip` | Playwright trace (`npx playwright show-trace trace.zip`) |

### Query job metadata

```sql
-- Jobs with salary info
SELECT title, company, salary_min, salary_max FROM job_posting
WHERE salary_min IS NOT NULL ORDER BY salary_max DESC;

-- Jobs mentioning PyTorch
SELECT title, company FROM job_posting
WHERE technologies @> '["PyTorch"]'::jsonb;

-- Jobs with ANY of these technologies
SELECT title, company, technologies FROM job_posting
WHERE technologies ?| array['Spark', 'Kubernetes', 'AWS'];
```

### Temporal UI

```
http://localhost:8233
```

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Enqueue a new application run |
| `GET` | `/runs/{id}` | Run status + current state |
| `GET` | `/runs/{id}/steps` | Step-by-step progress |
| `GET` | `/runs/{id}/artifacts` | All stored artifacts |

---

## Project Structure

```
src/yahbah/
├── api/                    # FastAPI control plane
│   ├── main.py             # App + lifespan (Temporal connection)
│   └── routes/             # jobs.py, runs.py
├── browser/                # Playwright automation
│   ├── manager.py          # BrowserRegistry singleton
│   └── greenhouse.py       # Auth, extraction, filling, submission
├── db/                     # SQLAlchemy models + async session
│   ├── models.py           # JobPosting, ApplicationRun, etc.
│   └── session.py          # AsyncSessionLocal
├── gmail/                  # Gmail API integration
│   ├── client.py           # GmailClient (OAuth2, polling, labels)
│   └── parser.py           # Verification code extraction
├── llm/                    # LLM integration
│   ├── client.py           # OllamaClient (structured + text generation)
│   ├── field_mapper.py     # Form field → profile mapping
│   └── cover_letter.py     # Cover letter generation
├── workflows/
│   ├── application.py      # ApplicationWorkflow state machine
│   └── activities/         # Temporal activities
│       ├── browser.py      # Open, extract, fill, submit
│       ├── db_ops.py       # State updates, dedup, artifacts
│       └── llm.py          # Field mapping, cover letter, metadata
├── config.py               # Settings (BaseSettings + .env)
├── schemas.py              # Temporal-serializable dataclasses
├── credentials.py          # Email alias + password generation
├── url_utils.py            # URL normalization + tracking stripping
└── worker.py               # Worker entrypoint
```
