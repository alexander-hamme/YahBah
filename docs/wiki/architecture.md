# Architecture Overview

YahBah takes a job URL and autonomously fills and submits the application, using local Ollama models, a headless Chrome driven by browser-use, and Temporal for durable orchestration. Everything is async (asyncio, psycopg3 via SQLAlchemy `postgresql+psycopg`, httpx).

## Processes
- **API**: FastAPI app `yahbah.api.main:app` (`src/yahbah/api/`). Enqueues Temporal workflows, serves run/application data. See [api-frontend](modules/api-frontend.md).
- **Worker**: `python -m yahbah.worker` (`src/yahbah/worker.py`). Runs the `ApplicationWorkflow` + activities and two background Gmail pollers. See [workflows](modules/workflows.md), [gmail](modules/gmail.md).
- **Frontend**: Next.js app in `frontend/`.
- **External**: Postgres, Temporal server, Ollama, Gmail API.

## Run flow
`POST /jobs` -> workflow `ApplicationWorkflow` (`src/yahbah/workflows/application.py`):
QUEUED (wait for run slot) -> DISCOVER -> MAP_FORM -> EXTRACT_METADATA -> CHECK_DUPLICATE -> MAP_ANSWERS -> GENERATE_COVER_LETTER (only if the form asks for one) -> FILL, then by the run's `submission_mode`, snapshotted when it is created from the runtime setting:
- `testing`: ends `COMPLETED` after FILL; nothing is submitted.
- `review` (the default, and what a missing or invalid setting means): the filled form's evidence is saved, the browser closes and the run waits as `AWAITING_REVIEW` (default 48 h). An approval queues for a run slot, re-fills exactly the approved answers and runs SUBMIT -> DONE. A required field nobody approved sends the run back to review; a rejection or an expired window submits nothing.

Browser stages call `yahbah.agent`; LLM stages call `yahbah.llm`. Statuses, reasons, the dedup predicate and the submission claim are in the [run-lifecycle contract](contracts/run-lifecycle.md).

## Core design rule: code first, models as fallbacks
Each browser stage tries deterministic code, then one structured model call, then an agent loop; each tier's output is verified in code before acceptance. No agent ever has a submit tool; submit is gated code (`agent/submit.py`), and structural page evidence, not a model, decides that it worked. Agents do have generic click and key actions, so every agent phase runs through `GuardedTools`, which refuses actions on submit or advance controls (one classifier, `agent/controls.py`); a post-agent tripwire records a possible submission if the page still changed. Details: [agent](modules/agent.md).

## Other invariants (verified in source)
- Page content/personal data stay local: telemetry, cloud sync and browser-use's PyPI version check (`BROWSER_USE_VERSION_CHECK=false`, which otherwise queries PyPI at every Agent start) are disabled in `src/yahbah/agent/__init__.py` (must be imported before `browser_use`).
- One browser session per run (`SessionRegistry`), in-process only, so it is lost when the worker stops. **Restart policy is resume:** the worker writes no run status when it starts or stops. The Temporal workflow stays alive and, when a worker is next available, retries the interrupted activity (within its 2-attempt retry policy); activities re-open the job URL and re-check the form. A run that had already claimed its submission only verifies, or ends `SUBMIT_UNCERTAIN`. Stale attempts are fenced out by a DB-level attempt lease. Details in [workflows](modules/workflows.md) and [contract §7](contracts/run-lifecycle.md).
- **Nothing is submitted without a code-recorded claim.** `run_state.claim_submission()` atomically sets `submit_attempted_at` and inserts a `submission_claim` row (one per posting, outliving its run) after the submit button is located and before the first click; no claim, no click. A retry after that only verifies, and re-clicks only on code-detected validation errors or code inputs. A run that attempted and has no code-verified success ends `SUBMIT_UNCERTAIN`, is never retried automatically, and blocks new applications to the posting (`possibly_applied()`) until the user resolves it ([contract §5, §6](contracts/run-lifecycle.md)).
- Each application uses a per-run email alias (`user+xxxx@...`, `src/yahbah/credentials.py`) so replies can be matched back.

## Revamp notes and pending change
- The Playwright `browser/` package, `llm/page_analyzer.py` and `config/site_templates/` were removed in `999f57a` (pre-revamp code lives on branch `archive/pre-agentic`); `src/yahbah/agent/` replaces them. `playwright` is not a dependency: browser-use 0.13.11 drives Chrome over CDP (`cdp-use`) and reuses a Playwright Chromium cache (`~/Library/Caches/ms-playwright`) if one is present.
- Only `greenhouse` and `generic` board profiles exist (`agent/boards.py`).
- Migration `0019` is the alembic head. `0018` (run lifecycle) and `0019` (answer-cache provenance) were applied to the dev database on 2026-10-20.
- Batch 1 of the roadmap is implemented. Wave 1 (QA harness, discovery gate, dashboard security) is committed in `d04bc45`; wave 2 (run lifecycle and review gate, submit boundary, answer integrity, review dashboard) in `d886823`, QA-verified. `src/yahbah/run_state.py` holds the shared vocabulary and rules. Next is batch 2 (the step-aware application loop, the `auto_reviewed` reviewer pass; see the roadmap's "Batch 1 status"). History and rationale: [changelog](changelog.md).
