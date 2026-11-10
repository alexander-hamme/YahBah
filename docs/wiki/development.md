# Development

## Run (three processes + deps)
`mkdir -p ~/.temporal` once, then `temporal server start-dev --db-filename ~/.temporal/yahbah.db` (without `--db-filename` the dev server is in-memory and loses queued and running workflows when it stops; the CLI does not create the directory); `uv run uvicorn yahbah.api.main:app --reload --port 8000`; `uv run python -m yahbah.worker`. Needs Postgres, Ollama with the configured models pulled, and a Chrome/Chromium for browser-use (it looks for system Chrome or Playwright's Chromium cache, `~/Library/Caches/ms-playwright` or `PLAYWRIGHT_BROWSERS_PATH`, and otherwise runs `uvx playwright install chromium` itself; pre-install with that command if you like). A run in the headed fallback opens a visible Chrome window, so the worker needs a GUI session for that (without one the run ends `blocked`). Full setup is in the root `README.md`. Setup: `uv sync`, `cp config/settings.example.yaml config/settings.yaml` (the runtime settings file is gitignored and machine-local; the code defaults apply without it, see [data-model](modules/data-model.md#configuration)), `uv run alembic upgrade head` (as DB owner, see [data-model](modules/data-model.md)), `uv run python scripts/seed_profile.py`.

## Tests (`test/`, pytest, asyncio_mode=auto; config in `pyproject.toml`)
Everything runs offline (guards below). Which run to use:

| Command | Runs | Time |
|---|---|---|
| `uv run pytest` | the default selection, in parallel: everything except the `e2e` tests (C7) | ~115 s (2026-10-25: 1145 passed, 3 skipped) |
| `uv run pytest --complete` | everything, including `e2e`, in parallel | not re-measured since 2026-10-20 (~35 s then, when the default run took the same) |
| `uv run pytest -n 0` (or `-p no:xdist`) | serially, for debugging; combines with `--complete`, paths and `-m` | not re-measured since 2026-10-20 (~280 s with `--complete` then) |
| `uv run pytest -m e2e` (or the path `test/invariants/c7_acceptance`) | only the C7 acceptance gate | not re-measured since 2026-10-20 (~21 s then) |

Runs are parallel by default: pytest-xdist (dev dependency) with `-n auto --dist load`, applied by `test/conftest.py` whenever no `-n` is given (not in `addopts`, so that `-p no:xdist` still parses). The suite is wait-bound (about 30% CPU serially), and `-n auto` measured clearly faster than a fixed `-n 8` here. The harness is per-process: each worker gets its own test database, fixture server, Temporal dev server and artifacts dir, and the offline-guard summary collects every worker's hits. Naming paths or node ids, or giving any `-m` expression, also runs the `e2e` tests it selects; only a bare run deselects them (`test/conftest.py`).

Layout:
- `test/yahbah/<area>/`: each module's own tests. Saved board pages are in `test/yahbah/fixtures/` (cases in `cases.json`; add one whenever a page is verified by hand). Synthetic ATS pages with scripted behaviour are in `fixtures/synthetic/`; each page documents its query parameters in a header comment. `fixtures/discover/` holds 18 pages for discover's dead-end classification (vendor bot walls, a 403 that says "not found", a soft 404, an SPA deep link, a closed-job text page with a working form, Greenhouse's closed-job and not-found pages, login and captcha walls, and so on). They are served with their own HTTP status and headers (below) and used by `test/yahbah/agent/test_discover_classification.py` (the pure `classify_page`), `test_discover_blocks.py` (discover end to end), `test_headed_fallback.py` and `test_headed_fallback_rules.py` (the headed fallback; its "headed" browser is a second headless Chrome injected through `SessionRegistry._start`, so no window opens), and `test/yahbah/workflows/test_headed_flag.py`.
- **Answer-engine tests** (all offline, `ScriptedLLM`): `test/yahbah/llm/test_facts.py` (normalization, validation messages without values, placeholders, profile facts and the alias), `test_answer_checks.py` (each C7 rule 1–10 with a boundary case, incl. quotes, absence and profile records), `test_answer_engine.py` (autofill skips the model, duplicate/missing Pass 1 ids, document routing, batching and prompt section headers, truncation split, call sequence and count of a recovery, cited-but-unshown facts shown in recovery, `to_artifact` holds no fact values), `test_migrate_known_answers.py` (the migration script on a synthetic `personal.yaml`), `test_answer_eval.py`'s two dataset tests (`test_dataset_is_well_formed` for the main and the held-out set, `test_heldout_items_are_new`), `test/yahbah/test_migration_0020.py` (`db`), and the adversarial invariants in `test/invariants/task5_answer_integrity/`. On the agent side: `test/yahbah/agent/test_form_schema_context.py` (`browser`, fixture `fixtures/synthetic/schema_context.html`: description, section, 30 options), the planned-text guard in `test_controls.py`, the planned-value lock and `NoMatchingOption` in `test_fill_logic.py`, and the option-miss replan in `test_fill_new_fields.py` (fixture `fixtures/synthetic/option_miss.html`). The live evals are below.
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
| `applicant_facts` | test | The synthetic applicant's `personal.yaml` (a typed `facts:` list, `harness/profile.py` `SYNTHETIC_FACTS`). `.set_facts(list)`, `.set_value(fact_id, value)`, `.add(fact)` and `.remove(fact_id)` rewrite it and clear the config cache, so the next plan sees the change. |
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
- `run_pipeline.py <url>`: run discover -> map -> answers -> fill without Temporal/run record; never submits without `--submit`; flags `--force-tier`, `--cover-letter`, `--headed`, `--headed-fallback` (enable discover's headed fallback for a page that blocks headless, contract §2), `--keep-open`; output `artifacts/debug-<ts>/`. It answers with the real engine over `load_facts` (the profile's real email, no alias) and writes `answer_plan.json` (the first plan, stage `map_answers`) and `answer_plan_fill_<n>.json` (each plan made while filling) there. A blocking plan prints each `label (status): reason` and stops, as a run would end `needs_answer`; an invalid or unmigrated `personal.yaml` prints the `FactsConfigError` message.
- `migrate_known_answers.py`: the one-time `known_answers:` → `facts:` migration (see below).
- `save_fixture.py <url> <name>`: save a live page as an offline fixture.
- `seed_profile.py`: seed applicant profile from the `profile` key of gitignored `config/personal.yaml` (merged by `load_prompts_config`; template: `config/personal.example.yaml`).
- `manual/gmail_verification.py <email>`: hand-run live check of verification-code retrieval, not collected by pytest.
- `imgs_to_gif.py`. (The Phase 0 browser-use spike script was removed in `999f57a`; it is still on branch `archive/pre-agentic`.)

## Migrating `known_answers` to `facts:` (one-time, user steps)
The two-pass answer engine reads only the typed `facts:` list in `config/personal.yaml`; the flat `known_answers:` map is no longer read, and a `personal.yaml` that still has it fails every answer plan with `FactsConfigError`. Until step 2 is done, live runs end at MAP_ANSWERS as `internal_error` carrying the migration instructions. Agents never read `personal.yaml`; these steps are the user's:
1. `uv run python scripts/migrate_known_answers.py`. It reads `settings.personal_path`, builds one fact per `known_answers` entry and writes `config/personal.facts-draft.yaml` (gitignored by `config/personal.*.yaml`). Kinds, data types, jurisdictions (a key suffix such as `_germany`, `_eu`; unsuffixed authorization, sponsorship and citizenship keys take the country of `country_of_residence`, default US), sensitivities, descriptions and guidance are guessed from the old key names by an ordered rule table that lives only in this tool (runtime never types a fact by key name). Entries are marked `# REVIEW:` (guessed kind, or a value that did not fit the guessed data type) or `# PLACEHOLDER: replace or delete`. It prints only counts and fact ids, never values, refuses to overwrite an existing draft without `--force`, validates the draft through `Fact`, prints "nothing to migrate" when there is no `known_answers:`, and never writes `personal.yaml`.
2. Review `config/personal.facts-draft.yaml`, move its `facts:` list into `config/personal.yaml` and delete `known_answers:` there. Edits to `personal.yaml` apply to the next plan without a restart. `config/personal.example.yaml` documents every fact field with synthetic examples (availability, relocation, authorization per jurisdiction, EEO, consent, employment history).
3. `uv run alembic upgrade head` (applies 0020, which drops `answer_cache`).

To check the result without Temporal: `uv run python scripts/run_pipeline.py <job-url>` (does not submit) and open `artifacts/debug-<ts>/answer_plan.json`: every non-generated answer has a non-empty `basis`, `calls` shows the model calls made (typically 3 per form batch, at most 6; one more when Pass 1 missed a field), and a blocking question prints with its reason.

## Live evals (answer engine; opt-in, `live_llm`)
Need Ollama running with both configured models (`ollama list`). They measure the real models; nothing mocks them. The how-to for running them (prerequisites, switches, run times, latest results) is [`test/yahbah/llm/README.md`](../../test/yahbah/llm/README.md).
- **Answers:** `YAHBAH_EVAL_OUT=artifacts/eval/answers-two-pass.json uv run pytest -m live_llm test/yahbah/llm/test_answer_eval.py -s` (`YAHBAH_EVAL_REPEATS=n` repeats). 77 items over three jobs, in `per_item` and `whole_form` mode. Gates in both: 0 critical-fact errors; `needs_answer` recall ≥ 95% on `screening_trap` + `personal_fact` + `experience_trap`; 100% of curveball and behavioural items answered, `generated=True`, no unsupported numbers or forbidden phrases; ≥ 95% of `screening_fact` + `fact` items answered; 0 `LLMError`s. An item may carry its own `facts:` list (extra facts only that item sees; `whole_form` groups items into forms by job + facts). The JSON report adds per-step seconds and tokens and calls per form. Measured wall time on the default models: 1797 s `per_item`, 780 s `whole_form` (2026-10-25, 64 items).
- **Held-out answers:** `YAHBAH_EVAL_SET=heldout YAHBAH_EVAL_OUT=artifacts/eval/answers-heldout.json uv run pytest -m live_llm test/yahbah/llm/test_answer_eval.py -s`. 20 items in `test/yahbah/llm/answer_eval_heldout.yaml`, same schema, modes and gates as the main set (`YAHBAH_EVAL_SET` is `main` by default; the other set's test is skipped). **Never tune a prompt against it:** run it once per prompt revision and report the result. If an item fails, fix the product against a new item in `answer_eval.yaml`, never by editing prompts, checks or the held-out file to suit the failing item, and never reword the held-out set after seeing a result. The person writing held-out items does not read `config/prompts.yaml`, and the person editing prompts does not read the held-out file; no prompt example may mirror an item of `answer_eval.yaml`, `verify_eval.yaml` or the held-out file. Offline, `uv run pytest test/yahbah/llm/test_answer_eval.py` checks both sets are well formed and that the held-out set shares no item id, label or job with the main set.
- **Verifier:** `uv run pytest -m live_llm test/yahbah/llm/test_verify_eval.py -s`. 55 proposals (29 seeded wrong, 26 correct; a proposal may carry `quotes` and `absent`), in two modes: `batch` (one call per job) and `single` (one call per proposal). `YAHBAH_VERIFY_MODES=batch,single` picks the modes, `YAHBAH_VERIFY_ROLE=driver|prose` overrides the checker for the test only (`MODEL_DRIVER=<model>` swaps the driver model), `YAHBAH_EVAL_REPEATS` and `YAHBAH_EVAL_OUT` as above. Gates per mode: catch rate ≥ 90% on the wrong ones, false rejection ≤ 10% on the correct ones.
- **If a gate fails:** compare checker models with the verifier eval (`YAHBAH_VERIFY_ROLE`, `MODEL_DRIVER`) before touching prompts; for the answers, only the four `answer_*` prompts may change (at most three iterations, each followed by both evals on the main set). The C7 checks, the recovery bound and the fail-closed rule are not relaxed. A held-out run never counts as an iteration: it is run once per prompt revision and reported. Details: [llm](modules/llm.md#tests-and-eval).

## Docs
- Root `README.md` is the human-facing entry point (setup, run, tests); this wiki holds the depth.
- Debugging an agent run: `artifacts/<run_id>/llm/raw.jsonl` and `steps/` ([llm](modules/llm.md)).
