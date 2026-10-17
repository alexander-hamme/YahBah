# YahBah

Autonomous job-application system: job URL in, form filled and submitted out. Stack: FastAPI + Temporal workflow/worker, browser-use over local Ollama models (code-first, models as fallbacks), Postgres (SQLAlchemy + psycopg3, alembic), Gmail polling, Next.js frontend. Branch `browser-use` is an in-progress "agentic revamp".

## Commands
- Deps: `uv sync`. Tests: `uv run pytest` (`-m "not browser"` skips headless-Chrome fixture tests).
- Run: `temporal server start-dev`; `uv run uvicorn yahbah.api.main:app --reload --port 8000`; `uv run python -m yahbah.worker`.
- Debug one URL without Temporal: `uv run python scripts/run_pipeline.py <url>` (does not submit unless `--submit`).
- Migrations: `uv run alembic upgrade head` (same `.env` role as the app; `yahbah_app` owns the DB and tables).

## Conventions
- Async-first; psycopg3; no over-engineering.
- Browser automation: deterministic code first, then one model call, then an agent loop; verify each tier in code. Agents never get a submit tool.
- Board-specific logic goes in `src/yahbah/agent/boards.py`, not generic code.
- Never read or copy secrets (`.env`, `config/personal*.yaml`, `credentials/`) into code or docs.
- Frontend: read `frontend/AGENTS.md` before writing Next.js code.
- `README.md` is the human-facing setup/run guide (rewritten for the revamp); depth lives in the wiki.

## Project Knowledge Base

The repository maintains a lightweight project wiki at
`docs/wiki/index.md`.

Use progressive disclosure: consult the index to identify relevant
documentation, read only the pages needed for the current task, and
verify important implementation details against source code.

Do not automatically load the entire wiki.

## Documentation Maintenance

When a development task materially changes architecture, interfaces,
dependencies, workflows, or important implementation behavior,
update the relevant wiki pages as part of the task.

Use the `project-wiki-manager` agent for substantial documentation
creation, architectural audits, or multi-page maintenance.

Minor fixes and routine refactoring generally do not require
documentation changes unless existing documentation becomes
inaccurate.

Do not commit or push changes without explicit permission.
