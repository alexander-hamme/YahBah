# YahBah — Autonomous Job Application System

End-to-end system that takes a Greenhouse job URL, fills and submits the application, and stores all artifacts and logs. Fully hands-off.

## Architecture

```
FastAPI (control plane)
    │
    └── Temporal (workflow orchestration)
            │
            ├── browser activities (Playwright / Greenhouse)
            ├── LLM activities (Ollama / field mapping + cover letter)
            └── DB activities (Postgres / state + artifacts)
```

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

Edit `.env` — defaults work for local dev if your Postgres user matches your OS username:

```
DATABASE_URL=postgresql+psycopg://localhost/yahbah
TEMPORAL_HOST=localhost:7233
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.3:70b
ARTIFACTS_DIR=./artifacts
```

### 3. Create the database and run migrations

```bash
createdb yahbah
uv run alembic upgrade head
```

### 4. Seed your applicant profile

Edit `scripts/seed_profile.py` with your real details (name, email, resume path, etc.), then:

```bash
uv run python scripts/seed_profile.py
```

`resume_path` must be an absolute path to your resume PDF.

### 5. Pull the LLM model

```bash
ollama pull llama3.3:70b
```

---

## Running

Start all three processes in separate terminals:

### Terminal 1 — Temporal dev server

```bash
temporal server start-dev
```

### Terminal 2 — API

```bash
uv run uvicorn yahbah.api.main:app --reload --port 8000
```

### Terminal 3 — Worker

```bash
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

Response:
```json
{"run_id": "uuid", "status": "PENDING", "job_url": "..."}
```

### Check run status

```bash
curl http://localhost:8000/runs/<run_id>
```

### List step-by-step progress

```bash
curl http://localhost:8000/runs/<run_id>/steps
```

### List artifacts

```bash
curl http://localhost:8000/runs/<run_id>/artifacts
```

Artifacts are stored in `./artifacts/<run_id>/`:

| File | Description |
|---|---|
| `screenshot_after_extract.png` | Form after extraction |
| `screenshot_before_fill.png` | Form before filling |
| `screenshot_before_submit.png` | Form after filling, before submit |
| `screenshot_confirmation.png` | Confirmation page |
| `form_schema.json` | Extracted form fields |
| `field_mappings.json` | LLM field mapping result |
| `cover_letter.txt` | Generated cover letter |
| `confirmation.txt` | Confirmation page text |
| `trace.zip` | Playwright trace (open with `npx playwright show-trace trace.zip`) |

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
| `GET` | `/health` | Health check |

---

## Swapping LLM Provider

To switch from Ollama to Anthropic/OpenAI, replace `OllamaClient._call_ollama` in
`src/yahbah/llm/client.py` with a call to the target SDK. `FieldMapper` and
`CoverLetterGenerator` are provider-agnostic — they call `OllamaClient` only.

---

## Notes

- Apple Silicon: consider switching from Ollama to MLX/mlx-lm for better M-series GPU utilisation
- llama.cpp is an alternative if you want tighter control over quantization
