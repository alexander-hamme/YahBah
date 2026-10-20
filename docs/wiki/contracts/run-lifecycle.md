# Contract: Run Lifecycle, Review Gate & Submission Safety

**Status: TARGET CONTRACT (agreed 2026-10-19), being implemented by batch 1** of the [2026-10-18 roadmap](../../audits/2026-10-18/roadmap.md) (tasks 1–6).
- **Until it is implemented**, source code shows current behaviour; this page shows the agreed target.
- **Once batch 1 lands**, this page becomes the authoritative description and the "TARGET" banner is removed.
- **Owner:** the main session. Changing any name, value or semantic here needs main-session approval first; agents report proposed changes, they don't make them unilaterally.

Guiding rules:
- **Code evidence decides; models advise.**
- **Nothing is submitted without a code-recorded claim.**
- **"May have been submitted" is never treated as "not applied".**
- **Replace, don't layer:** superseded code (`testing_mode`, `is_test_run`, the worker's FAILED sweep, retry-in-place) is removed, not kept alongside the new code.

## 1. Run status (`application_run.status`, text)

| Status | Terminal | Meaning |
|---|---|---|
| `PENDING` | no | Created; workflow started; waiting for a run slot |
| `RUNNING` | no | Executing a stage. **Only this status holds a run slot.** |
| `AWAITING_REVIEW` | no | Filled; evidence saved; browser closed; waiting for approve or reject (§4) |
| `COMPLETED` | yes | Finished the work its mode asks for: submitted and verified by code evidence, or in `testing` mode, filled |
| `SUBMIT_UNCERTAIN` | yes | `submit_attempted_at` is set but success was not verified by code evidence. Never auto-retried; resolved only per §6. |
| `FAILED` | yes | Ended **before** any submit claim (`submit_attempted_at IS NULL`) |
| `CANCELLED` | yes | The user cancelled, or rejected in review, before any submit claim |
| `EXPIRED` | yes | The review window elapsed without approval; nothing was submitted |
| `DUPLICATE` | yes | `possibly_applied()` (§5) matched another run |

**Invariant:** whatever the cause (error, cancel, restart, timeout), a run whose `submit_attempted_at` is set and that has no code-verified success ends `SUBMIT_UNCERTAIN`, never `FAILED` or `CANCELLED`.

**Who writes status:**
- **The workflow's own activities only,** fenced (§7).
- **API routes never write status directly.** Cancel, approve, reject and retry go through the workflow or create a new run. There are exactly two exceptions, both fenced on the expected current status:
  - **Resolving an uncertain run** (§6): runs `UPDATE … WHERE id = :run AND status = 'SUBMIT_UNCERTAIN'`. This is safe because the run's workflow has already ended.
  - **Reconciliation** (§7): only when the workflow no longer exists.
- **The worker writes no status at start or stop** (resume policy).

## 2. `failure_reason` (new column, text, nullable)

Set on every terminal status except `COMPLETED`. `error_message` keeps the human-readable detail.

| Group | Values |
|---|---|
| Discover/map | `form_not_found`, `form_unmapped`, `form_changed`, `job_closed`, `login_wall`, `captcha`, `blocked` |
| Answers | `needs_answer` |
| Fill | `fill_failed`, `possibly_submitted` (agent tripwire: the page navigated or the form vanished while an agent acted) |
| Submit | `submit_button_not_found`, `claim_denied`, `email_code_entry`, `email_code_timeout`, `submit_unverified` |
| Infra | `browser_error`, `llm_error`, `timeout`, `superseded`, `internal_error` |
| User/policy | `cancelled`, `review_rejected`, `review_expired`, `duplicate` |

The single source of these values is `src/yahbah/run_state.py` (§10). An owning task may add a value only by reporting it to the main session.

## 3. `submission_mode` and `origin`

- **Runtime setting `submission_mode`** (`config/settings.yaml`, via `get_runtime_settings`) replaces `testing_mode`, which is removed everywhere.
  - Values in batch 1: `testing | review`. `auto_reviewed` is added in batch 2 together with the reviewer pass.
  - A missing or invalid value means **`review`** (fail closed). `PUT /settings/submission_mode` validates the value.
- **Snapshot:** each run records `application_run.submission_mode` at creation, and `ApplicationWorkflowInput.submission_mode` carries the same value. Changing the global setting affects only new runs.
  - `testing`: the run ends `COMPLETED` after FILL.
  - `review`: §4.
- **`is_test_run` is dropped** (migration 0018 backfills `submission_mode='testing'` where it was true). Runs created before the contract have `submission_mode = NULL`, meaning legacy.
- **`origin`** (`manual | discovered`) is set at creation:
  - `POST /jobs` → `manual`;
  - scheduled-job promotion → `discovered`;
  - a retry inherits it from the original run.

## 4. Review gate (`submission_mode = review`)

**Workflow:** QUEUED → … → FILL. The FILL activity saves the fill evidence (§8) and the filled-form screenshot, then closes the browser. The run then:
1. writes `AWAITING_REVIEW` and `review_deadline = now + review_window_hours` (runtime setting, default 48), and releases its run slot;
2. waits with `workflow.wait_condition(lambda: self._decision is not None, timeout=review_window)`.

**Review rounds:** every entry into `AWAITING_REVIEW` increments `review_round` (an int, starting at 1, stored on the run). A round's evidence and screenshot are captured fresh, and the screenshot is a full-page capture.

**Signals** on `ApplicationWorkflow`:
- `approve_submission(review_round: int)`
- `reject_submission(review_round: int, reason: str | None)`

The workflow ignores, and logs, any signal whose round is not the current round. An approval therefore covers exactly the `answers` and `new_fields` the user saw in that round: all or nothing, with no per-answer edits in batch 1. To change an answer, the user rejects, corrects the stored answer or profile, and retries. Per-answer editing comes in batch 2.

**API** (the routes send signals; the workflow writes the resulting state):

| Route | Allowed from | Effect |
|---|---|---|
| `GET /runs/{id}/review` | `AWAITING_REVIEW` (readable afterwards too) | Review payload (§8), including `review_round` |
| `POST /runs/{id}/approve` (body `{review_round}`) | `AWAITING_REVIEW` with a matching round, else 409 | Signals approve |
| `POST /runs/{id}/reject` (body `{review_round, reason?}`) | `AWAITING_REVIEW` with a matching round, else 409 | Signals reject → `CANCELLED` / `review_rejected` |

**Timeout:** `EXPIRED` / `review_expired`. Nothing is submitted on timeout, ever.

**Approve path:**
1. Acquire a run slot, then go to `RUNNING`.
2. Re-open the browser, re-discover, and check the form is unchanged (`form_changed` otherwise).
3. **Re-fill exactly the approved mappings**, with no new model mapping.
4. If a required field without an approved answer appears, map it, add it to the evidence as `new_fields`, and **return to `AWAITING_REVIEW`** with a fresh deadline. Repeat as needed.
5. Otherwise submit (§6).

**Interface between tasks 3 and 4:** `fill_form(..., map_new_fields=None)` records such fields on `ctx.unmapped_new_fields: list[dict]`, with the same row shape as schema fields, instead of failing. The FILL/submit activity returns them to the workflow as `FillOutput.unmapped_new_fields: list[dict]`, and the workflow turns them into the review loop.

**The review window** is `review_window_hours: float`. Like `submission_mode`, it is snapshotted at run creation into `ApplicationWorkflowInput.review_window_hours`, so the workflow stays deterministic. Fractional values are valid; tests use sub-hour windows.

**Unverified submit:** `submit_and_verify` raises `StageFailed("submit_unverified")`. It never returns a flagged result.

**After the submit claim**, nothing new may be mapped: in review mode `submit_and_verify` gets `map_new_fields=None`. A post-click problem ends `SUBMIT_UNCERTAIN`.

## 5. `possibly_applied()`, the one dedup predicate

```python
@dataclass(frozen=True)
class Blocker:
    run_id: UUID | None   # None when only a claim remains (its run was deleted)
    status: str | None
    reason: Literal["in_flight", "completed", "submit_uncertain", "attempted", "email_confirmed", "claim"]

async def possibly_applied(session, job_posting_id: UUID, *, exclude_run_id: UUID | None = None) -> Blocker | None
async def possibly_applied_url(session, url: str, *, exclude_run_id: UUID | None = None) -> Blocker | None  # resolves the posting by normalized URL
```

Returns what blocks a new application, or `None`. Both functions live in `run_state.py`. A run blocks when **all** of these hold:
- it is a different run (not `exclude_run_id`);
- it is for the same posting;
- its `submission_mode` is not `testing`;
- **and** at least one of:
  - its status is in flight: `PENDING`, `RUNNING` or `AWAITING_REVIEW`;
  - its status is `COMPLETED` or `SUBMIT_UNCERTAIN`;
  - `submit_attempted_at` or `email_confirmed_at` is set, whatever the status.

A `submission_claim` row for the posting (§6) also blocks.

`FAILED`, `CANCELLED`, `EXPIRED` and `DUPLICATE` runs without a claim do **not** block.

**Every dedup site calls it:**
- `check_duplicate_activity`;
- `POST /jobs` (409 with the existing run id);
- `POST /scheduled`;
- the alert poller's "already applied" check;
- scheduled promotion.

Testing-mode runs never block a real one.

## 6. Submission claim and resolving uncertainty

```python
async def claim_submission(run_id: str, workflow_id: str, job_posting_id: UUID) -> bool  # in run_state.py
```

Called once, immediately before the first submit click, **after the submit button has been located**. It is one transaction:
1. `UPDATE application_run SET submit_attempted_at = now() WHERE id = :run AND temporal_workflow_id = :wf AND submit_attempted_at IS NULL AND status = 'RUNNING'`
2. `INSERT INTO submission_claim (posting_key, run_id, claimed_at) VALUES (:posting, :run, now()) ON CONFLICT DO NOTHING`

It returns `True` only if both affected one row; otherwise it rolls back and returns `False`.

**In-browser interface:** `submit_and_verify(..., claim: Callable[[], Awaitable[bool]], ...)` replaces `mark_attempted`. If `claim()` returns `False`, no click happens and it raises `StageFailed("claim_denied")`.

**`posting_key`:** in batch 1 it is `str(job_posting_id)`. B2's identity key `(ats, tenant, job_id)` replaces it later.

**Claims outlive runs:** `run_id` references `application_run` with `ON DELETE SET NULL`, so deleting a run never removes duplicate protection.

**Activity retries within the same workflow:** `already_attempted=True` means verify-only. A re-click then requires code evidence: code-detected validation errors, or verification-code inputs.

**Resolving `SUBMIT_UNCERTAIN`:**
- `POST /runs/{id}/confirm-submitted` → `COMPLETED`.
- `POST /runs/{id}/confirm-not-submitted` → `FAILED`, keeping its `failure_reason`, and deletes the claim. This is the only path that re-enables applying to that posting.
- An alias-matched confirmation email moves `SUBMIT_UNCERTAIN` → `COMPLETED`. This is the poller's job (batch 2, B7).

## 7. Run ownership, retry and cancel

- **Fenced writes:** every status or state write from an activity runs as `UPDATE … WHERE id = :run AND temporal_workflow_id = :wf AND status NOT IN (<terminal>)`. If zero rows change, the activity raises a non-retryable `ApplicationError(type="superseded")`.
- **Browser ownership:** `SessionRegistry` entries carry an owner `(workflow_id, attempt)`, taken from `activity.info()` by default.
  - `open(run_id, url)` and `get(run_id)` keep their call shape.
  - The latest attempt of the owning workflow owns the session: a higher attempt takes it over, and any older attempt's `get` returns `None`, so the older attempt fails as `superseded`.
  - A different workflow id never gets it.
- **Restart policy is resume.** The worker writes no status at start or stop, and sets a `graceful_shutdown_timeout`.
- **Long activities heartbeat:** every activity with `start_to_close` over 10 min (discover, map_form, map_answers, fill, submit) has a `heartbeat_timeout` of 2–3 min, kept alive by a background heartbeat task. A dead worker is then detected in minutes, and a timed-out attempt is cancelled locally before any retry.
- **Timeouts are module-level constants** in `workflows/application.py`, so tests can patch them.
- **Retry** (`POST /runs/{id}/retry`):
  - Allowed only from `FAILED`, `CANCELLED`, `EXPIRED` or `DUPLICATE` with `submit_attempted_at IS NULL`, or from a `COMPLETED` testing-mode run. Anything else gets 409.
  - It terminates the original workflow if it is still running, then creates a **new run** with the same posting and `origin`, `retry_of_run_id = original`, the current `submission_mode`, and a fresh alias.
  - The original row, its alias and its evidence are untouched. The response returns the new run id.
- **Cancel** (`POST /runs/{id}/cancel`): requests workflow cancellation. The workflow's cancellation path closes the browser and writes `CANCELLED`, or `SUBMIT_UNCERTAIN` if a claim exists.
- **Reconciliation:** if the workflow no longer exists, the API reconciles the row itself: `CANCELLED` if unclaimed, `SUBMIT_UNCERTAIN` if claimed.
- **One active run per posting:** a partial unique index on `application_run(job_posting_id) WHERE status IN ('PENDING','RUNNING','AWAITING_REVIEW')`.

## 8. Evidence and API shapes

**Fill evidence.** The `submitted_values` artifact, written by FILL, is the single per-field record for both review and post-submit display. Its rows:

```json
{"key": "…", "label": "…", "type": "text", "required": true, "options": [],
 "answer": "intended value", "source": "known-answer key | profile key | resume | cover_letter | custom | skip",
 "basis": ["stored keys/profile fields that back the answer"], "generated": false,
 "written": "what code typed/selected", "page_value": "read back from the page", "ok": true}
```

**`FieldMapping`** (`src/yahbah/schemas.py`) gains `basis: list[str] = []` and `generated: bool = False`. Task 5 populates them, and everyone else passes them through unchanged.
- `generated = True`: model-written prose, for open-ended and personality questions.
- `basis`: required for any non-generated factual answer.

**`GET /runs/{id}/review`:**

```json
{"run_id": "…", "status": "AWAITING_REVIEW", "submission_mode": "review", "review_round": 1, "review_deadline": "ISO-8601",
 "job": {"title": "…", "company": "…", "url": "…"},
 "answers": [<fill evidence rows>], "new_fields": [<rows needing approval>],
 "screenshot_artifact_id": "…", "cover_letter": {"text": "…", "artifact_id": "…"} | null}
```

**`outcome`** is derived on the server by `derive_outcome()` and returned on the run list and detail responses, alongside these fields:
- `failure_reason`;
- `submission_mode`;
- `origin`;
- `submit_attempted_at`;
- `review_deadline`;
- `retry_of_run_id`.

The frontend renders `outcome`; it never derives it from `status`.

| status | condition | outcome |
|---|---|---|
| PENDING | | `queued` |
| RUNNING | | `in_progress` |
| AWAITING_REVIEW | | `awaiting_review` |
| COMPLETED | `submission_mode = testing` | `test_only` |
| COMPLETED | `email_confirmed_at` set | `email_verified` |
| COMPLETED | otherwise | `submitted` |
| SUBMIT_UNCERTAIN | | `submit_uncertain` |
| FAILED / CANCELLED / EXPIRED / DUPLICATE | | `failed` / `cancelled` / `expired` / `duplicate` |

**`actions`:** the run list and detail responses also carry `actions: list[Action]`, from `allowed_actions()` in `run_state.py`. Values: `cancel`, `retry`, `approve`, `reject`, `confirm_submitted`, `confirm_not_submitted`, `delete`. The UI shows only the actions listed and never re-derives eligibility. The routes enforce the same function, returning 409 otherwise.

**Listing and stats** work by outcome:
- `GET /applications` accepts a multi-value `outcome` filter and `sort=review_deadline_asc`.
- `/stats` counts by outcome.

**Error bodies:** every 4xx raised by the routes has the shape `{"detail": {"code": "<snake_case>", "message": "<human text>", "run_id": "<blocking or affected run, optional>"}}`. Examples:
- `already_applied`: `POST /jobs` or `/scheduled`, with the blocking run's id;
- `not_awaiting_review`;
- `stale_review_round`;
- `action_not_allowed`;
- `invalid_url`.

**Settings:** `GET /settings` returns the **effective** values: `submission_mode` (`review` when missing or invalid) and `review_window_hours`. `PUT /settings/{key}` validates through `validate_runtime_setting()` in `config.py` and rejects unknown keys.

**Artifacts:** the download route serves HTML artifacts as `text/plain` with `Content-Security-Policy: sandbox`, never as `text/html`.

## 9. Schema changes

**Migration `0018_run_lifecycle` (task 3, `down_revision = "0017"`):**
- **`application_run` additions:**
  - `submission_mode` (text, nullable; `NULL` = legacy);
  - `origin` (text, nullable);
  - `failure_reason` (text, nullable);
  - `review_deadline` (timestamptz, nullable);
  - `retry_of_run_id` (uuid, nullable, references `application_run` `ON DELETE SET NULL`).
- **Also added:** `review_round` (int, nullable).
- **Schema drift repair:** `job_posting.canonical_url` and `job_posting.location` are mapped by the ORM and present in the production DB, but no migration creates them, so a fresh `alembic upgrade head` lacks both (QA harness finding). Migration 0018 adds them with `ADD COLUMN IF NOT EXISTS`, and the harness's `KNOWN_DRIFT` bridge is then deleted.
- **Backfill:**
  - `submission_mode = 'testing'` where `is_test_run`;
  - `origin = 'discovered'` for runs referenced by `scheduled_job.promoted_run_id`, else `'manual'`;
  - `FAILED` with `submit_attempted_at` set becomes `SUBMIT_UNCERTAIN` (0 such rows exist today, but the rule must hold);
  - `FAILED` with `error_message = 'Cancelled by user'` becomes `CANCELLED` / `cancelled`.
- **Drop:** `is_test_run`.
- **New table:** `submission_claim (posting_key text PRIMARY KEY, run_id uuid NULL REFERENCES application_run ON DELETE SET NULL, claimed_at timestamptz NOT NULL)`.
- **New index:** the partial unique index from §7.
- **Foreign key:** `scheduled_job.promoted_run_id` becomes `ON DELETE SET NULL`, which unblocks deleting promoted runs.

**Migration `0019_answer_cache_provenance`** belongs to task 5 (`down_revision = "0018"`). Integration order is task 3, then task 5.

## 10. Shared code location

`src/yahbah/run_state.py` holds:
- the status, `failure_reason`, `submission_mode`, `origin`, `outcome` and `Action` literals;
- the terminal-status set;
- `derive_outcome()` and `allowed_actions()`;
- `possibly_applied()` / `possibly_applied_url()`.

Before wave 2 starts, the main session lands a skeleton with the literals, `derive_outcome()` and the `FieldMapping` fields; task 3 implements the rest.

## 11. Batch-1 file ownership

Agents edit only their own files. Each agent writes tests for its own modules under the matching `test/yahbah/<area>/` directory; the shared harness belongs to task 1.

| Task | Owns |
|---|---|
| 1 QA harness | `test/conftest.py`, `test/harness/**`, `test/invariants/**`, pytest config in `pyproject.toml` |
| 2 Discovery gate | `src/yahbah/gmail/**`, `src/yahbah/url_utils.py`; the URL-policy call sites in `api/routes/jobs.py`, `api/routes/scheduled.py`, `jobs.py`; new alert settings keys in `config.py` |
| 3 Run lifecycle | `src/yahbah/workflows/**`, `worker.py`, `db/**`, `alembic/versions/0018_*`, `run_state.py`, `schemas.py`, `agent/session.py`, `api/routes/runs.py`, `jobs.py`, the dedup sites in `gmail/alert_poller.py` and `api/routes/scheduled.py` (after task 2), `config.py` (`submission_mode`, `review_window_hours`) |
| 4 Submit boundary | `src/yahbah/agent/submit.py`, `agent/fill.py`, `agent/tools.py` |
| 5 Answer integrity | `src/yahbah/llm/**`, `config/prompts.yaml`, `config/personal.example.yaml`, `alembic/versions/0019_*`, `scripts/run_pipeline.py` (cache namespace) |
| 6 Dashboard | `frontend/**`; `src/yahbah/api/main.py` (middleware, settings endpoints wired to `config.py`'s validation); `src/yahbah/api/routes/applications.py` (outcome filter, stats, sort, `outcome`/`actions` on list rows) |

**Shared files:**
- `schemas.py` and `run_state.py` are changed only through the main session's skeleton, or by task 3.
- `submit.py` ↔ `activities/browser.py`: they interact only through the interfaces in §4 and §6.
- Wiki module pages: each task updates the page for its own module.
