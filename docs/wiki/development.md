# Development

## Run (three processes + deps)
`mkdir -p ~/.temporal` once, then `temporal server start-dev --db-filename ~/.temporal/yahbah.db` (without `--db-filename` the dev server is in-memory and loses queued and running workflows when it stops; the CLI does not create the directory); `uv run uvicorn yahbah.api.main:app --reload --port 8000`; `uv run python -m yahbah.worker`. Needs Postgres, Ollama with the configured models pulled, and a Chrome/Chromium for browser-use (it looks for system Chrome or Playwright's Chromium cache, `~/Library/Caches/ms-playwright` or `PLAYWRIGHT_BROWSERS_PATH`, and otherwise runs `uvx playwright install chromium` itself; pre-install with that command if you like). A run in the headed fallback opens a visible Chrome window, so the worker needs a GUI session for that (without one the run ends `blocked`). Full setup is in the root `README.md`. Setup: `uv sync`, `cp config/settings.example.yaml config/settings.yaml` (the runtime settings file is gitignored and machine-local; the code defaults apply without it, see [data-model](modules/data-model.md#configuration)), `uv run alembic upgrade head` (as DB owner, see [data-model](modules/data-model.md)), `uv run python scripts/seed_profile.py`.

## Tests (`test/`, pytest, asyncio_mode=auto; config in `pyproject.toml`)
Everything runs offline (guards below). Which run to use:

| Command | Runs | Time |
|---|---|---|
| `uv run pytest` | the default selection, in parallel: everything except the `e2e` tests (C7) | ~80 s (2026-10-23: 1094 passed, 2 skipped) |
| `uv run pytest --complete` | everything, including `e2e`, in parallel | not re-measured since 2026-10-20 (~35 s then, when the default run took the same) |
| `uv run pytest -n 0` (or `-p no:xdist`) | serially, for debugging; combines with `--complete`, paths and `-m` | not re-measured since 2026-10-20 (~280 s with `--complete` then) |
| `uv run pytest -m e2e` (or the path `test/invariants/c7_acceptance`) | only the C7 acceptance gate | not re-measured since 2026-10-20 (~21 s then) |

Runs are parallel by default: pytest-xdist (dev dependency) with `-n auto --dist load`, applied by `test/conftest.py` whenever no `-n` is given (not in `addopts`, so that `-p no:xdist` still parses). The suite is wait-bound (about 30% CPU serially), and `-n auto` measured clearly faster than a fixed `-n 8` here. The harness is per-process: each worker gets its own test database, fixture server, Temporal dev server and artifacts dir, and the offline-guard summary collects every worker's hits. Naming paths or node ids, or giving any `-m` expression, also runs the `e2e` tests it selects; only a bare run deselects them (`test/conftest.py`).

Layout:
- `test/yahbah/<area>/`: each module's own tests. Saved board pages are in `test/yahbah/fixtures/` (cases in `cases.json`; add one whenever a page is verified by hand). Synthetic ATS pages with scripted behaviour are in `fixtures/synthetic/`; each page documents its query parameters in a header comment. `fixtures/discover/` holds 18 pages for discover's dead-end classification (vendor bot walls, a 403 that says "not found", a soft 404, an SPA deep link, a closed-job text page with a working form, Greenhouse's closed-job and not-found pages, login and captcha walls, and so on). They are served with their own HTTP status and headers (below) and used by `test/yahbah/agent/test_discover_classification.py` (the pure `classify_page`), `test_discover_blocks.py` (discover end to end), `test_headed_fallback.py` and `test_headed_fallback_rules.py` (the headed fallback; its "headed" browser is a second headless Chrome injected through `SessionRegistry._start`, so no window opens), and `test/yahbah/workflows/test_headed_flag.py`.
- `test/harness/`: the shared harness (the package docstrings are the reference). Its own tests are in `harness/selftest/`.
- `test/invariants/task<N>_*/`: the safety invariants of the [run-lifecycle contract](contracts/run-lifecycle.md) and roadmap A2–A5/A7, grouped by owning task. `batch1_review/`: defects found by QA's close-out review of tasks 3 and 4 (2026-10-20), each test naming its owning task.
- `test/invariants/c7_acceptance/`: roadmap C7, the acceptance gate before any unattended live submission (below).

**Markers.** A test that uses the matching fixture gets its marker automatically:
- `browser`: headless Chrome.
- `db`: the test Postgres.
- `temporal`: a local Temporal dev server.
- `live_llm`: real local models. Opt-in only: `-m live_llm` or `YAHBAH_LIVE_LLM=1`; skipped otherwise.

Set by hand:
- `slow`: waits for a real timeout to expire (no time-skipping Temporal server is available offline). In the default run; `-m "not slow"` skips them.
- `e2e`: whole runs through Temporal with the real worker, every real activity and Chrome (C7). Left out of a bare run.
- An opt-in live check opens a real headed Chrome window: `YAHBAH_LIVE_BROWSER=1` (end of `test/yahbah/agent/test_headed_fallback.py`); skipped otherwise.

Deselect with `-m "not browser"`, `-m "not db"`, `-m "not temporal"`.

**C7, the acceptance gate** (`test/invariants/c7_acceptance/`). `yahbah.worker.main()` in-process on a local Temporal server with **no activity stubbed**; real headless Chrome launched by `SessionRegistry` as in production, with `jobs.harness.example` mapped onto the fixture server and every other host unresolvable; the mock-ATS form with an instant success page; `ScriptedLLM` for every model call; a `GmailClient` that fails the run if a code is asked for; the real `/jobs` and `/runs` routes. It asserts: a `testing` run ends `COMPLETED` with no claim and no click; a `review` run reaches `AWAITING_REVIEW` with the §8 evidence rows and a PNG screenshot, and an approval of round 1 gives exactly one submit click, one claim, the `submit_evidence.jsonl` artifact (closing verdict decided by code) and `COMPLETED`, after which `POST /jobs` for the same job is `409 already_applied`; a rejected run ends `CANCELLED`/`review_rejected` and a sub-hour review window ends `EXPIRED`, with nothing sent. The only timing change from production is `SessionRegistry.open()`'s fixed 3 s render wait, capped at 0.5 s.

**Guards** (`harness/env.py`, installed by `test/conftest.py` before yahbah is imported; they apply to every test):
- `DATABASE_URL` points at a nonexistent database, so the real `yahbah` DB is unreachable.
- DNS works only for localhost, and Python sockets connect only to loopback. Names under `harness.example` resolve to a public IP that nothing can connect to, so job URLs pass the intake policy. Anything blocked is listed at the end of the run under "offline guard".
- Chrome resolves only localhost and runs without browser-use's default extensions.
- `OllamaClient` and `ChatOllama` raise outside `live_llm` tests.
- Artifacts go to a temp dir, and `PERSONAL_PATH` points at the synthetic applicant (`harness/profile.py`). `config/personal*.yaml` is never read.

**Fixtures** (`harness/fixtures.py`):

| Fixture | Scope | Gives |
|---|---|---|
| `fixture_server` | session | stdlib HTTP server on 127.0.0.1 serving `test/yahbah/fixtures/`. `events_named("submit")` returns what pages reported via `fetch('/__event?name=…')`. |
| `pages` | test | A per-test page directory on that server: `pages.write(name, html, status=200, headers=None) -> url` (a non-200 status or extra headers make the server answer with them, for a WAF interstitial or a 404; a `Server` header is honoured) and `pages.redirect(name, to, status=302) -> url`. `FixtureServer.respond`, `.redirect` and `.forget` do the same per path; `unmount` clears them. |
| `browser` | module | browser-use session; load pages with `harness.browser.load(session, url)`. browser-use doesn't index `about:blank`, so pages are always served over HTTP. |
| `llm` | test | `ScriptedLLM` on `OllamaClient`: `llm.on(role, schema, replies=[…] / handler=…)`. Calls are recorded; an unscripted call raises, and a swallowed one fails the test. |
| `agent_llm` | test | `AgentScript` as every browser-use chat model: `.click(id=…)`, `.send_keys(…)`, `.done()`. Drives `run_agent` and `fix_with_agent` for real. |
| `applicant_facts` | test | The synthetic stored answers; `.update(key=value)` rewrites them. |
| `runtime_settings` | test | `config/settings.yaml` redirected to a temp file; `.set(submission_mode=…)`, `.unset(key)`. |
| `pg_database` / `db` | session / test | A disposable `yahbah_test_<hex>` database (see below). `db` truncates it, patches every `AsyncSessionLocal`, and offers `seed_posting`, `seed_run`, `seed_profile` (the synthetic applicant as the `ApplicantProfile` row), `insert`, `run`, `statuses_seen`, `peak_running`. |
| `temporal_env` | module | `WorkflowEnvironment.start_local` with the `temporal` binary on PATH. A time-skipping server only via `YAHBAH_TEMPORAL_TEST_SERVER`; nothing is ever downloaded, and the tests skip without one. |
| `workflows` | test | The real `ApplicationWorkflow` under the real `yahbah.worker.main()`. Browser/LLM activities are stubbed under their real names; db_ops activities are real. `enqueue()` goes through `submit_job_for_application`. Also `stubs.on/gate`, `worker.restart()`, `wait_status`, `wait_closed`. |
| `activity_env` | test | `activity_env(workflow_id=…, attempt=…).call(fn, *args)` runs `fn` inside an activity context; `.cancel()` cancels it. |

Route tests use `harness.api.api_client(temporal=…)`: every route of `yahbah.api.main.app` with the real app's exception handlers (so error bodies are the §8 ones, including `invalid_request`), without its lifespan and middleware. `harness.activities` has `FakeBrowserSession` and inputs for activity-level tests of the browser activities.

**Test database.**
- `TEST_DATABASE_URL` (default `postgresql+psycopg://yahbah_test@localhost/postgres`) is used only to `CREATE` and `DROP` the session's `yahbah_test_<hex>` database.
- The database is migrated with `alembic upgrade head` in a subprocess, and dropped at session end.
- If Postgres is unreachable, `db` tests skip.
- A trigger on `application_run` writes every status change to `harness_status_log`, so "never shown FAILED" and "peak RUNNING" are exact, not polled.
- Every ORM column must come from a migration: `test_schema.py` fails on drift (migration 0018 added the two columns that had none).
- Migration tests use `harness.db.create_database_at("0017")`: a second database migrated only that far, seeded with plain SQL, then `migrate(db, "head")`. See `test_migration_backfill.py`.

**Flipping an invariant.** A test of a known, unfixed defect is marked `@defect("<contract § or roadmap id>", task, "<defect>")` (`harness/contract.py`), a strict `xfail`. **None is marked today:** all 91 wave-1 xfails and QA's close-out findings were flipped when batch 1 landed (some docstrings in `test/invariants/batch1_review/` still say "strict xfail until fixed"). Every test in `test/invariants/` is now a guard. The pure `run_state` rules are tested in `test/yahbah/test_run_state.py`. When a fix makes a marked test pass, the run fails with XPASS(strict).
1. Remove the mark. For parametrized cases, remove the mark only from the cases that now pass.
2. If the implementation chose a name the contract leaves open, adjust the test. Examples: the prose reply schema, the 409 code for a refused action (the tests assume `action_not_allowed`), or the `Blocker.reason` when several apply.

The harness's bridges for pre-contract interfaces (`testing_mode`, `is_test_run`, `mark_attempted`, `KNOWN_DRIFT`) were deleted when tasks 3 and 4 landed.

`--runxfail` shows the real failure of every invariant.

**Frontend.** Its own checks (`npm test`, `npm run lint`, `npx tsc --noEmit`, the real-browser sandbox check) are in [api-frontend](modules/api-frontend.md).

## Scripts (`scripts/`)
- `run_pipeline.py <url>`: run discover -> map -> answers -> fill without Temporal/run record; never submits without `--submit`; flags `--force-tier`, `--cover-letter`, `--headed`, `--headed-fallback` (enable discover's headed fallback for a page that blocks headless, contract §2), `--keep-open`; output `artifacts/debug-<ts>/`.
- `save_fixture.py <url> <name>`: save a live page as an offline fixture.
- `seed_profile.py`: seed applicant profile from the `profile` key of gitignored `config/personal.yaml` (merged by `load_prompts_config`; template: `config/personal.example.yaml`).
- `manual/gmail_verification.py <email>`: hand-run live check of verification-code retrieval, not collected by pytest.
- `imgs_to_gif.py`. (The Phase 0 browser-use spike script was removed in `999f57a`; it is still on branch `archive/pre-agentic`.)

## Docs
- Root `README.md` is the human-facing entry point (setup, run, tests); this wiki holds the depth.
- Debugging an agent run: `artifacts/<run_id>/llm/raw.jsonl` and `steps/` ([llm](modules/llm.md)).
