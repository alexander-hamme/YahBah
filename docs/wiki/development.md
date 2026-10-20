# Development

## Run (three processes + deps)
`mkdir -p ~/.temporal` once, then `temporal server start-dev --db-filename ~/.temporal/yahbah.db` (without `--db-filename` the dev server is in-memory and loses queued and running workflows when it stops; the CLI does not create the directory); `uv run uvicorn yahbah.api.main:app --reload --port 8000`; `uv run python -m yahbah.worker`. Needs Postgres, Ollama with the configured models pulled, and a Chrome/Chromium for browser-use (it looks for system Chrome or Playwright's Chromium cache, `~/Library/Caches/ms-playwright` or `PLAYWRIGHT_BROWSERS_PATH`, and otherwise runs `uvx playwright install chromium` itself; pre-install with that command if you like). Full setup is in the root `README.md`. Setup: `uv sync`, `uv run alembic upgrade head` (as DB owner, see [data-model](modules/data-model.md)), `uv run python scripts/seed_profile.py`.

## Tests (`test/`, pytest, asyncio_mode=auto; config in `pyproject.toml`)
`uv run pytest` runs everything offline in about a minute. Layout:
- `test/yahbah/<area>/`: each module's own tests. Saved board pages are in `test/yahbah/fixtures/` (cases in `cases.json`; add one whenever a page is verified by hand). Synthetic ATS pages with scripted behaviour are in `fixtures/synthetic/`; each page documents its query parameters in a header comment.
- `test/harness/`: the shared harness (the package docstrings are the reference). Its own tests are in `harness/selftest/`.
- `test/invariants/task<N>_*/`: the safety invariants of the [run-lifecycle contract](contracts/run-lifecycle.md) and roadmap A2–A5/A7, grouped by owning task.

**Markers.** A test that uses the matching fixture gets its marker automatically:
- `browser`: headless Chrome.
- `db`: the test Postgres.
- `temporal`: a local Temporal dev server.
- `live_llm`: real local models. Opt-in only: `-m live_llm` or `YAHBAH_LIVE_LLM=1`; skipped otherwise.

Deselect with `-m "not browser"`, `-m "not db"`, `-m "not temporal"`.

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
| `pages` | test | A per-test page directory on that server: `pages.write(name, html) -> url`. |
| `browser` | module | browser-use session; load pages with `harness.browser.load(session, url)`. browser-use doesn't index `about:blank`, so pages are always served over HTTP. |
| `llm` | test | `ScriptedLLM` on `OllamaClient`: `llm.on(role, schema, replies=[…] / handler=…)`. Calls are recorded; an unscripted call raises, and a swallowed one fails the test. |
| `agent_llm` | test | `AgentScript` as every browser-use chat model: `.click(id=…)`, `.send_keys(…)`, `.done()`. Drives `run_agent` and `fix_with_agent` for real. |
| `applicant_facts` | test | The synthetic stored answers; `.update(key=value)` rewrites them. |
| `runtime_settings` | test | `config/settings.yaml` redirected to a temp file; `.set(submission_mode=…)`, `.unset(key)`. |
| `pg_database` / `db` | session / test | A disposable `yahbah_test_<hex>` database (see below). `db` truncates it, patches every `AsyncSessionLocal`, and offers `seed_posting`, `seed_run`, `insert`, `run`, `statuses_seen`, `peak_running`. |
| `temporal_env` | module | `WorkflowEnvironment.start_local` with the `temporal` binary on PATH. A time-skipping server only via `YAHBAH_TEMPORAL_TEST_SERVER`; nothing is ever downloaded, and the tests skip without one. |
| `workflows` | test | The real `ApplicationWorkflow` under the real `yahbah.worker.main()`. Browser/LLM activities are stubbed under their real names; db_ops activities are real. `enqueue()` goes through `submit_job_for_application`. Also `stubs.on/gate`, `worker.restart()`, `wait_status`, `wait_closed`. |
| `activity_env` | test | `activity_env(workflow_id=…, attempt=…).call(fn, *args)` runs `fn` inside an activity context. |

**Test database.**
- `TEST_DATABASE_URL` (default `postgresql+psycopg://yahbah_test@localhost/postgres`) is used only to `CREATE` and `DROP` the session's `yahbah_test_<hex>` database.
- The database is migrated with `alembic upgrade head` in a subprocess, and dropped at session end.
- If Postgres is unreachable, `db` tests skip.
- A trigger on `application_run` writes every status change to `harness_status_log`, so "never shown FAILED" and "peak RUNNING" are exact, not polled.
- `harness.db.KNOWN_DRIFT` adds ORM columns no migration creates. Today that is `job_posting.canonical_url` and `location`; migration 0018 repairs them, and `test_schema.py` holds the fix.
- Migration tests use `harness.db.create_database_at("0017")`: a second database migrated only that far, seeded with plain SQL, then `migrate(db, "head")`. See `test_migration_backfill.py`.

**Flipping an invariant.** Every invariant test of an unfixed defect is marked `@defect("<contract § or roadmap id>", task, "<defect>")`, a strict `xfail`. Unmarked tests in `test/invariants/` are guards that already hold, for example the 3 submit shapes today's guard blocks. The pure `run_state` rules are tested in `test/yahbah/test_run_state.py`. When the owner's change makes a marked test pass, the run fails with XPASS(strict).
1. Remove the mark. For parametrized cases, remove the mark only from the cases that now pass.
2. If the implementation chose a name the contract leaves open, adjust the test. Examples: the prose reply schema, the 409 code for a refused action (the tests assume `action_not_allowed`), or the `Blocker.reason` when several apply.
3. Delete the harness bridges for the pre-contract interfaces once nothing uses them:
   - the `mark_attempted` branch of `harness.forms.submit`;
   - the `testing_mode` key written by `RuntimeSettings` and by `Stubs.default`;
   - `TestDB._legacy_run_values` (`is_test_run`);
   - the `KNOWN_DRIFT` entries.

`--runxfail` shows the real failure of every invariant.

**Frontend.** Its own checks (`npm test`, `npm run lint`, `npx tsc --noEmit`, the real-browser sandbox check) are in [api-frontend](modules/api-frontend.md).

## Scripts (`scripts/`)
- `run_pipeline.py <url>`: run discover -> map -> answers -> fill without Temporal/run record; never submits without `--submit`; flags `--force-tier`, `--cover-letter`, `--headed`, `--keep-open`; output `artifacts/debug-<ts>/`.
- `save_fixture.py <url> <name>`: save a live page as an offline fixture.
- `seed_profile.py`: seed applicant profile from the `profile` key of gitignored `config/personal.yaml` (merged by `load_prompts_config`; template: `config/personal.example.yaml`).
- `manual/gmail_verification.py <email>`: hand-run live check of verification-code retrieval, not collected by pytest.
- `imgs_to_gif.py`. (The Phase 0 browser-use spike script was removed in `999f57a`; it is still on branch `archive/pre-agentic`.)

## Docs
- Root `README.md` is the human-facing entry point (setup, run, tests); this wiki holds the depth.
- Debugging an agent run: `artifacts/<run_id>/llm/raw.jsonl` and `steps/` ([llm](modules/llm.md)).
