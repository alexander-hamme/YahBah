# Architecture Overview

YahBah takes a job URL and autonomously fills and submits the application, using local Ollama models, a headless Chrome driven by browser-use, and Temporal for durable orchestration. Everything is async (asyncio, psycopg3 via SQLAlchemy `postgresql+psycopg`, httpx).

## Processes
- **API**: FastAPI app `yahbah.api.main:app` (`src/yahbah/api/`). Enqueues Temporal workflows, serves run/application data. See [api-frontend](modules/api-frontend.md).
- **Worker**: `python -m yahbah.worker` (`src/yahbah/worker.py`). Runs the `ApplicationWorkflow` + activities and two background Gmail pollers. See [workflows](modules/workflows.md), [gmail](modules/gmail.md).
- **Frontend**: Next.js app in `frontend/`.
- **External**: Postgres, Temporal server, Ollama, Gmail API.

## Run flow
`POST /jobs` -> workflow `ApplicationWorkflow` (`src/yahbah/workflows/application.py`):
QUEUED (wait for run slot) -> DISCOVER -> MAP_FORM -> EXTRACT_METADATA -> CHECK_DUPLICATE -> MAP_ANSWERS -> GENERATE_COVER_LETTER -> FILL -> SUBMIT -> DONE. Testing mode (runtime setting `testing_mode`) ends after FILL. Browser stages call `yahbah.agent`; LLM stages call `yahbah.llm`.

## Core design rule: code first, models as fallbacks
Each browser stage tries deterministic code, then one structured model call, then an agent loop; each tier's output is verified in code before acceptance. No agent ever has a submit tool; submit is gated code (`agent/submit.py`). Details: [agent](modules/agent.md).

## Other invariants (verified in source)
- Page content/personal data stay local: telemetry/cloud sync disabled in `src/yahbah/agent/__init__.py` (must be imported before `browser_use`).
- One browser session per run (`SessionRegistry`), in-process only; a worker restart marks RUNNING runs FAILED (`worker.fail_incomplete_runs`).
- `submit_attempted_at` is written before the first submit click; retries after that only verify.
- Each application uses a per-run email alias (`user+xxxx@...`, `src/yahbah/credentials.py`) so replies can be matched back.

## In flux (working tree, uncommitted)
- Old Playwright `browser/` package, `llm/page_analyzer.py`, and `config/site_templates/` are deleted; replaced by `src/yahbah/agent/`. The `playwright` package is no longer a dependency: browser-use 0.13.11 drives Chrome over CDP (`cdp-use`) and only reuses Playwright's Chromium cache if one is present.
- Only `greenhouse` and `generic` board profiles exist (`agent/boards.py`).
- Migration `0017` (answer cache, submit tracking) is untracked.
