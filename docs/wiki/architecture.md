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
Each browser stage tries deterministic code, then one structured model call, then an agent loop; each tier's output is verified in code before acceptance. No agent ever has a submit tool; submit is gated code (`agent/submit.py`). **Limit today:** agents do have the generic `click`/`send_keys` actions, and the only guard against an agent activating a submit control is in-page JS (`GUARD_SUBMIT_JS` in `agent/fill.py`), applied around the fill-fix agent only and covering native form submits only (audit application-forms F5). Batch 1 task 4 adds an action-level guard ([roadmap](../audits/2026-10-18/roadmap.md) A4). Details: [agent](modules/agent.md).

## Other invariants (verified in source)
- Page content/personal data stay local: telemetry, cloud sync and browser-use's PyPI version check (`BROWSER_USE_VERSION_CHECK=false`, which otherwise queries PyPI at every Agent start) are disabled in `src/yahbah/agent/__init__.py` (must be imported before `browser_use`).
- One browser session per run (`SessionRegistry`), in-process only, so it is lost when the worker stops. **Restart, current behaviour:** `worker.fail_incomplete_runs` marks `RUNNING` rows FAILED on worker start and stop, but the Temporal workflow stays alive and, when a worker is next available, retries the interrupted activity (within its 2-attempt retry policy). Activities re-open the job URL and re-check the form; the next stage update sets the row back to `RUNNING`. Details in [workflows](modules/workflows.md). Batch 1 replaces this with a pure resume policy ([contract §7](contracts/run-lifecycle.md)).
- `submit_attempted_at` is written before the first submit click (`db_ops.mark_submit_attempted`, a plain UPDATE, not an atomic claim). **Current behaviour:** an activity retry after that skips the first click and verifies, but the verify loop can still click submit again after an email-code entry or a `needs_fix` repair (`agent/submit.py`). A lost browser after the click is a non-retryable `submit_unverified`. Batch 1 replaces this with an atomic claim ([contract §6](contracts/run-lifecycle.md)).
- Each application uses a per-run email alias (`user+xxxx@...`, `src/yahbah/credentials.py`) so replies can be matched back.

## Revamp notes and pending change
- The Playwright `browser/` package, `llm/page_analyzer.py` and `config/site_templates/` were removed in `999f57a` (pre-revamp code lives on branch `archive/pre-agentic`); `src/yahbah/agent/` replaces them. `playwright` is not a dependency: browser-use 0.13.11 drives Chrome over CDP (`cdp-use`) and reuses a Playwright Chromium cache (`~/Library/Caches/ms-playwright`) if one is present.
- Only `greenhouse` and `generic` board profiles exist (`agent/boards.py`).
- Migration `0017` (answer cache, submit tracking) is committed and is the alembic head.
- Batch-1 wave 1 (QA harness, discovery gate, dashboard security) has landed in the working tree; wave 2 (run lifecycle and review gate, submit boundary, answer integrity) is next. The wave-2 target is specified in [contracts/run-lifecycle.md](contracts/run-lifecycle.md); where it differs from this page, the source shows current behaviour. `src/yahbah/run_state.py` is the contract's code skeleton: the status/outcome/action vocabulary and the pure `derive_outcome()` / `allowed_actions()` work, while `possibly_applied()`, `possibly_applied_url()` and `claim_submission()` raise `NotImplementedError` until task 3. Nothing under `src/` imports it yet. History and rationale: [changelog](changelog.md).
