# Contract: Run Lifecycle, Review Gate & Submission Safety

**Status: IMPLEMENTED and authoritative** (agreed 2026-10-19; implemented by batch 1 of the [2026-10-18 roadmap](../../audits/2026-10-18/roadmap.md), amended through 2026-10-24; verified against the code on 2026-10-20). The 2026-10-24 amendments (§2 Answers row, §4 "Planned values only", §8 `FieldMapping` and `answer_plan`) describe the two-pass answer engine.
- **Source of truth:** this page and `src/yahbah/run_state.py` agree. A difference between this page and the code is a defect to report to the main session, not a licence to pick one.
- **Owner:** the main session. Changing any name, value or semantic here needs main-session approval first; agents report proposed changes, they don't make them unilaterally.
- **History:** the decisions behind the amendments are in the [changelog](../changelog.md), "Batch 1, wave 2 + close-out (2026-10-20)".

Guiding rules:
- **Code evidence decides; models advise.**
- **Nothing is submitted without a code-recorded claim.**
- **"May have been submitted" is never treated as "not applied".**
- **Replace, don't layer:** superseded code (`testing_mode`, `is_test_run`, the worker's FAILED sweep, retry-in-place) was removed, not kept alongside the new code.

## 1. Run status (`application_run.status`, text)

| Status | Terminal | Meaning |
|---|---|---|
| `PENDING` | no | Created; workflow started; waiting for a run slot |
| `RUNNING` | no | Executing a stage. **Only this status holds a run slot.** |
| `AWAITING_REVIEW` | no | Filled; evidence saved; browser closed; waiting for approve or reject (§4) |
| `COMPLETED` | yes | Finished the work its mode asks for: submitted and verified by code evidence, or in `testing` mode, filled |
| `SUBMIT_UNCERTAIN` | yes | `submit_attempted_at` is set but success was not verified by code evidence, or the agent tripwire fired (§6). Never auto-retried; resolved only per §6. |
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

Set on every terminal status except `COMPLETED`. `error_message` keeps the human-readable detail. A run forced to `SUBMIT_UNCERTAIN` by the §1 invariant keeps the reason that ended it (`cancelled`, `timeout`, `browser_error`, ...).

| Group | Values |
|---|---|
| Discover/map | `form_not_found`, `form_unmapped`, `form_changed`, `job_closed`, `login_wall`, `captcha`, `blocked` |
| Answers | `needs_answer` (`error_message` lists every blocking question as `label (status)`; the `answer_plan` artifact, §8, carries the reasons) |
| Fill | `fill_failed`, `possibly_submitted` (agent tripwire: the page navigated or the form vanished while an agent acted; always ends `SUBMIT_UNCERTAIN`, §6) |
| Submit | `submit_button_not_found`, `claim_denied`, `email_code_entry`, `email_code_timeout`, `submit_unverified` |
| Infra | `browser_error`, `llm_error`, `timeout`, `superseded`, `internal_error` |
| User/policy | `cancelled`, `review_rejected`, `review_expired`, `duplicate` |

The single source of these values is `src/yahbah/run_state.py` (§10). An owning task may add a value only by reporting it to the main session.

**Discover-stage classification** (added 2026-10-21, after run `83a0efc2` labelled an Akamai 403 wall `job_closed`). **Code decides.** Only page evidence (main-document HTTP status, vendor markup, board URL markers, visible controls) sets these reasons; text on a page is never evidence. One pure entry point, `classify_page(PageEvidence, board) -> PageVerdict | None` in `agent/discover.py`, covers every reason. Its `signal` and `detail` (for example `http_403`, vendor, host) go into `error_message`.

Precedence, highest first:
1. **A visible application form** wins over any status or text: no failure.
2. **A vendor bot-wall signature** at any status → `blocked`. The signatures are Akamai, Cloudflare challenge or error, PerimeterX/HUMAN, DataDome and Imperva, and they cover vendor captcha walls too.
3. **Main-document 401/403/407/429 on an inert page** → `blocked`. A page is inert when it has **at most 5 visible controls** (`INERT_MAX_CONTROLS`, the same gate as rule 4; changing it is a contract change), so an SPA that answers a deep link with 403 or 404 and then renders isn't caught.
4. **404/410 on an inert page** → `job_closed`.

   **Board route first** (applies to rules 2–4): when a wrapper page carries a board id, the board's own form is tried before rules 2–4 decide. The id comes from `gh_jid`, or from a job-id-shaped number in the URL path (for example `job.8274601` or `/jobs/8274601`). Either way, the (board, id) pair is confirmed **in the browser**: the board's own form page is loaded and must pass the checks below. No side-channel HTTP or API calls are made (user decision, 2026-10-21). An unknown pair lands on the board's closure marker and counts as no route. The one exception is when **both** halves are explicit: the id comes from `gh_jid`, the token is named by the page itself (not guessed from the domain), and the board's marker page loads (HTTP 200). Then the board itself says the job is gone, and the run ends `job_closed`. Every other miss records `board route: closed on <token>` in the detail and leaves the reason to rules 2–4, because a guessed token or a path number can't prove closure. If it yields the verified form, the run proceeds. If it yields nothing, rules 2–4 decide on the wrapper's evidence, and the detail records `board route: none`, so a board-token miss can be told apart from a gone job. A resolved page is used only if its host is the board's, the job id appears in its URL or page, and the form signature is present. Otherwise discover returns to the wrapper and the detail records `board route: unverified`. Opening another job's form is never acceptable.
5. **The board's closure marker** (in `boards.py`; Greenhouse: a `greenhouse.io` board root or `embed/job_board` URL with `error=true`) → `job_closed`.
6. **A visible captcha widget on the site's own page** → `captcha`: a reCAPTCHA anchor iframe not sized invisible, an hCaptcha checkbox, or Turnstile. reCAPTCHA v3 badges don't count.
7. **A visible password field with no application form** → `login_wall`.
8. **Plain 5xx with no vendor signature**, or a browser net error or crash → `browser_error`. A `chrome-error://` document has no HTTP status, so discover maps it explicitly.
9. **Otherwise** → `form_not_found`.

Rules 2–5 and 8 run before any model tier, with zero model calls. Rules 6–7 run only after the tiers fail, since a guest-apply path may exist. A model's `job_closed`, `captcha` or `login_required` answer is **advisory**: it goes in the detail and is never the reason, and an uncertain or "other" answer is `form_not_found`. `job_closed` therefore always rests on positive evidence; soft-404 pages and "no longer accepting" text are not enough.

**Retryability:**
- `blocked`, `job_closed`, `captcha` and `login_wall` are non-retryable activity errors. Retrying within seconds only hammers a WAF. The user can still retry the FAILED run later (§7).
- `form_not_found` and `browser_error` are retryable, with the existing bounded retries.

**No new value:** a 429 stays `blocked`, and its signal says so.

**Never evade:** YahBah does not use stealth, fingerprint spoofing, captcha solving or proxy rotation. A protected page is reached only through a legitimate route, such as the board's own public form, or the run ends `blocked`.
- **Default launch flags:** browser-use's defaults, which include disabling Blink's `AutomationControlled`, are accepted as they are. This is the user's decision of 2026-10-21. YahBah adds no masking of its own.
- **Headed fallback** (the user's decision, 2026-10-21; order: board route, then headed, then `blocked`). When discover's early verdict is `blocked` (rules 2–3) and no board route verifies, discover reopens the URL once in a **fresh, throwaway, headed Chromium**. It has no saved profile: a temporary user-data-dir, deleted when the session closes. It uses the same launch flags as the main session (browser-use's defaults, see above) and adds no masking of its own. If that page passes rules 1–3, the run continues **headed for the rest of its life**, with the normal workflow: navigation tiers, map, fill, review, and the approved re-fill and submit. If the headed page is also walled, the run ends `blocked` with `headed: blocked`.
  - It never fires for `job_closed`, `captcha`, `login_wall` or `browser_error`. There is no captcha solving and no retry loop: one headed attempt per discover.
  - If headed Chromium can't launch (no GUI session, a locked screen, the worker under launchd), the run ends `blocked` with `headed: unavailable`.
  - It is gated by the runtime setting `browser_headed_fallback` (default `true`), which is snapshotted at run creation like `submission_mode`.
  - **Interface** (`src/yahbah/schemas.py`):
    - `DiscoverOutput.headed: bool = False`. The workflow keeps it in its state and passes `headed` on every later activity input that opens a browser (`MapFormInput`, `FillInput` and `SubmitInput`; `map_new_fields` is model-only and opens no browser). The snapshotted setting reaches discover as `DiscoverInput.headed_fallback: bool = False`. Re-opens in later stages use the run's mode and never fall back themselves, so only discover changes it.
    - `SessionRegistry.open(run_id, url, *, headed: bool = False)`.
    - `headed=True` always uses a fresh temporary profile. Cookies never carry over between sessions or runs.
    - The flag lives in workflow history, not the DB.
    - A retry (§7) starts headless again.
  - The run's evidence and trace record `headed` so the user can see it. Every `blocked` verdict also records its signal and whether the URL carried a board id.

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

The workflow ignores, and logs, any signal whose round is not the current round. An approval therefore covers exactly the `answers` and `new_fields` the user saw in that round: all or nothing, with no per-answer edits in batch 1. To change an answer, the user rejects, corrects the stored fact (`config/personal.yaml` `facts:`) or profile, and retries. Per-answer editing comes in batch 2.

**API** (the routes send signals; the workflow writes the resulting state):

| Route | Allowed from | Effect |
|---|---|---|
| `GET /runs/{id}/review` | `AWAITING_REVIEW` (readable afterwards too) | Review payload (§8), including `review_round` |
| `POST /runs/{id}/approve` (body `{review_round}`) | `AWAITING_REVIEW` with a matching round, else 409 | Signals approve |
| `POST /runs/{id}/reject` (body `{review_round, reason?}`) | `AWAITING_REVIEW` with a matching round, else 409 | Signals reject → `CANCELLED` / `review_rejected` |

**Triage (quick decisions from the queue)** (added 2026-10-20). The review queue may approve or reject runs from a compact preview, without opening the full review. There is **no bulk route**: every decision is its own `POST /runs/{id}/approve` or `/reject`, so a stale round is a per-run 409. Rules:
- The `review_round` sent is the one in the **`GET /runs/{id}/review` payload the preview rendered**, never the list row's `review_round`. If the payload is refetched with a new round, any pending decision for the old round is dropped.
- A run can be approved only after its own preview has rendered **without an error and stayed on screen for a minimum dwell** (about 800 ms) at its current round. A new round, or a switch to another run, restarts the dwell. Key repeats never count as a decision. No "approve all" or "approve selected" sends approvals for runs the user has not previewed. Holding or double-pressing a key must not carry a decision onto the next run.
- The preview shows every answer the full review would flag: new fields, page mismatches, required-but-blank fields, answers without a stored basis, and generated answers, with the text of each generated answer.
- A quick approval is deliberate: it waits through a short, visible undo window before the request is sent. The window replaces the tick box of the full review. Nothing is sent if the user undoes, leaves the page, or the run leaves `AWAITING_REVIEW` first. Every dropped decision is reported visibly, not only to screen readers. A request whose outcome is unknown, such as after a network error, is reported as unknown, never as "not sent".
- Rejecting needs no reason, and a quick reject gets the same undo window and round guard. Hiding the tab counts as leaving the page. Opening the full review is always one step away, and doing so cancels that run's pending quick decision.

**Timeout:** `EXPIRED` / `review_expired`. Nothing is submitted on timeout, ever.

**Approve path:**
1. Go to `PENDING` while waiting for a run slot, then to `RUNNING` once it has one.
2. Re-open the browser, re-discover, and check the form is unchanged (`form_changed` otherwise).
3. **Re-fill exactly the approved mappings**, with no new model mapping.
4. If a required field without an approved answer appears, map it, add it to the evidence as `new_fields`, and **return to `AWAITING_REVIEW`** with a fresh deadline. Repeat as needed.
5. Otherwise submit (§6).

**Interface between tasks 3 and 4:** `fill_form(..., map_new_fields=None)` records such fields on `ctx.unmapped_new_fields: list[dict]`, with the same row shape as schema fields, instead of failing. The FILL/submit activity returns them to the workflow as `FillOutput.unmapped_new_fields: list[dict]`, and the workflow turns them into the review loop.

**Approved-state baseline** (added 2026-10-20; extended to first fills 2026-10-24). In every fill, locked or not, `fill_form` records what every field *without* a planned answer (no mapping, `skip`, or an empty value) shows once code has filled, new fields and option replans included (`FillContext.baseline`), before any agent runs. A field without a planned answer whose content then differs from its baseline is a fill problem, whatever write path changed it, so a first fill fails closed (`fill_failed`) rather than showing the reviewer an agent's stray write. On a locked re-fill the baseline travels as data: `FillOutput.baseline` → `SubmitInput.baseline` → `submit_and_verify`, so the submit gate and every post-claim fix round can tell an agent's write from a starting value. `agent/fill.py` owns its shape. A locked submit without a baseline fails closed (`fill_failed`) before the gate and the claim. A re-fill that submit has to redo itself (browser lost between re-fill and submit) takes its own baseline. `locked` additionally governs new-field handling and the `wrote_from` check.

**The review window** is `review_window_hours: float`. Like `submission_mode`, it is snapshotted at run creation into `ApplicationWorkflowInput.review_window_hours`, so the workflow stays deterministic. Fractional values are valid; tests use sub-hour windows.

**Unverified submit:** `submit_and_verify` raises `StageFailed("submit_unverified")`. It never returns a flagged result.

**After the submit claim**, nothing new may be mapped: in review mode `submit_and_verify` gets `map_new_fields=None`. A post-click problem ends `SUBMIT_UNCERTAIN`.

**Planned values only** (added 2026-10-24, with the two-pass answer engine). In every fill, first or locked, the fix agent's `fill_field` writes only the planned value of a field that has a plan entry, and refuses a field without one. Its free-text `input` action may type only text contained in a planned value (`FillContext.text_allowed`, refused with `VALUE_NOT_PLANNED` and logged to `guard.jsonl` otherwise). A planned value that matches no option of the live widget is re-answered once through the answer engine, with the options read from the page and the miss as feedback (first fill only, never on a locked re-fill); it is never chosen by an agent. A second miss is an ordinary fill problem.

## 5. `possibly_applied()`, the one dedup predicate

```python
@dataclass(frozen=True)
class Blocker:
    run_id: UUID | None   # None when only a claim remains (its run was deleted)
    status: str | None
    reason: Literal["in_flight", "completed", "submit_uncertain", "attempted", "email_confirmed", "claim"]

async def possibly_applied(session, job_posting_id: UUID, *, exclude_run_id: UUID | None = None) -> Blocker | None
async def possibly_applied_url(session, url: str, *, exclude_run_id: UUID | None = None) -> Blocker | None  # every posting whose url or canonical_url is `url` or its canonical form
```

Returns what blocks a new application, or `None`. Both functions live in `run_state.py`. A run blocks when **all** of these hold:
- it is a different run (not `exclude_run_id`);
- it is for the same posting;
- its `submission_mode` is not `testing`;
- **and** at least one of:
  - its status is in flight: `PENDING`, `RUNNING` or `AWAITING_REVIEW`;
  - its status is `COMPLETED` or `SUBMIT_UNCERTAIN`;
  - `submit_attempted_at` or `email_confirmed_at` is set, whatever the status.

A `submission_claim` row for the posting (§6) also blocks, unless it is `exclude_run_id`'s own. When several runs block, the reported `reason` is the first of `in_flight`, `completed`, `submit_uncertain`, `attempted`, `email_confirmed` (then `claim`).

`FAILED`, `CANCELLED`, `EXPIRED` and `DUPLICATE` runs without a claim do **not** block.

**Every dedup site calls it:**
- `check_duplicate_activity`, for the posting, every posting with the same canonical URL, and (within `duplicate_window_days`) every posting with the same title + company + location;
- `POST /jobs` (409 with the existing run id) and scheduled promotion, both through `submit_job_for_application` (§7);
- `POST /scheduled`;
- the alert poller's "already applied" check.

Testing-mode runs never block a real one.

## 6. Submission claim and resolving uncertainty

```python
async def claim_submission(run_id: str, workflow_id: str, job_posting_id: UUID) -> bool  # in run_state.py
```

Called once, immediately before the first submit click, **after the submit button has been located**. It is one transaction:
1. `UPDATE application_run SET submit_attempted_at = now() WHERE id = :run AND temporal_workflow_id = :wf AND submit_attempted_at IS NULL AND status = 'RUNNING' AND <the caller holds the attempt lease, §7>`
2. `INSERT INTO submission_claim (posting_key, run_id, claimed_at) VALUES (:posting, :run, now()) ON CONFLICT DO NOTHING`

It returns `True` only if both affected one row; otherwise it rolls back and returns `False`.

**In-browser interface:** `submit_and_verify(..., claim: Callable[[], Awaitable[bool]], ...)` replaces `mark_attempted`. If `claim()` returns `False`, no click happens and it raises `StageFailed("claim_denied")`.

**`posting_key`:** in batch 1 it is `str(job_posting_id)`. B2's identity key `(ats, tenant, job_id)` replaces it later.

**Claims outlive runs:** `run_id` references `application_run` with `ON DELETE SET NULL`, so deleting a run never removes duplicate protection.

**Activity retries within the same workflow:** `already_attempted=True` means verify-only. A re-click then requires code evidence: code-detected validation errors, or verification-code inputs.

**Agent tripwire** (added 2026-10-19 for tasks 3 and 4). Agents never click submit: an action-level guard blocks it in every agent phase. If an agent action still navigates the page or makes the form vanish, the in-browser code raises `StageFailed("possibly_submitted")`. A submission may have happened without a claim, so the activity first calls `record_possible_submission(run_id, workflow_id, job_posting_id) -> None` (in `run_state.py`). That call does two things in one transaction:
- a fenced `UPDATE … SET submit_attempted_at = now() WHERE submit_attempted_at IS NULL`;
- `INSERT INTO submission_claim … ON CONFLICT DO NOTHING`.

The run then ends `SUBMIT_UNCERTAIN` / `possibly_submitted`, never `FAILED`.
- `possibly_submitted` is always non-retryable, even when recording it fails.
- The terminal write treats `failure_reason = possibly_submitted` as attempted on its own: it records the claim if it is missing and writes `SUBMIT_UNCERTAIN`.
- A cancellation that arrives after an agent left the form still trips the wire.

**Per-round submit evidence.** `submit_and_verify(..., record: Callable[[dict], Awaitable[None]])` calls `record` once per verify round with a JSON-serializable evidence dict, then once more for the final evidence pass. Task 4 owns the dict's shape and documents it in `modules/agent.md`. The submit activity (task 3) appends each dict to a `submit_evidence.jsonl` run artifact as it arrives, so the evidence survives a lost worker.

**Verification codes.** `submit_and_verify` calls `GmailClient.wait_for_verification_code(..., code_pattern=board.email_code_pattern, exclude_message_ids=<ids already tried>)`, enters the code, and calls `finalize_code_message(message_id)` only after the page accepts it. The `fetch_verification_code` compatibility wrapper has been deleted.

**Resolving `SUBMIT_UNCERTAIN`:**
- `POST /runs/{id}/confirm-submitted` → `COMPLETED`.
- `POST /runs/{id}/confirm-not-submitted` → `FAILED`, keeping its `failure_reason`. It also deletes the claim and clears `submit_attempted_at`, keeping the original attempt time in `error_message`, so the run neither blocks the posting nor breaks the §1 rule that FAILED means never attempted. This is the only path that re-enables applying to that posting.
- An alias-matched confirmation email moves `SUBMIT_UNCERTAIN` → `COMPLETED`. **Not implemented in batch 1** (batch 2, B7): the status poller sets `email_confirmed_at` (which keeps blocking the posting) but does not change the status.

## 7. Run ownership, retry and cancel

- **Fenced writes:** every status or state write from an activity runs as `UPDATE … WHERE id = :run AND temporal_workflow_id = :wf AND <lease held> AND status NOT IN (<terminal>)`. If zero rows change, the activity raises a non-retryable `ApplicationError(type="superseded")`.
- **Attempt lease** (amended 2026-10-20 after the QA review): each activity attempt that acts on a run takes the run's lease when it starts (`RunLeaseInterceptor`, registered on the worker; `close_session_activity` is exempt). The lease is the owner `(workflow_id, activity sequence, attempt)`, stored on `application_run` in columns added by migration 0018.
  - Taking the lease is monotonic: an older owner can never overwrite a newer one.
  - Every fenced write and `claim_submission()` also require that the caller still holds the lease, so a timed-out attempt cannot claim or write once its retry has started, on this worker or any other.
  - `record_possible_submission()` sets `submit_attempted_at` only while the caller holds the lease, but it **always** inserts the claim. A possible submission must block the posting whoever recorded it.
- **Browser ownership:** `SessionRegistry` entries carry an owner `(workflow_id, activity sequence, attempt)`, taken from `activity.info()` by default. The sequence is needed because Temporal's attempt number restarts for every activity.
  - `open(run_id, url)` and `get(run_id)` keep their call shape.
  - The latest attempt of the owning workflow owns the session: a higher attempt takes it over, and any older attempt's `get` returns `None`, so the older attempt fails as `superseded`.
  - A different workflow id never gets it.
- **Restart policy is resume.** The worker writes no status at start or stop, and sets a `graceful_shutdown_timeout`.
- **Long activities heartbeat:** every activity with `start_to_close` over 10 min (discover, map_form, map_answers, map_new_fields, fill, submit) has a `heartbeat_timeout` of 2–3 min, kept alive by a background heartbeat task. A dead worker is then detected in minutes, and a timed-out attempt is cancelled locally before any retry.
- **Timeouts are module-level constants** in `workflows/application.py`, so tests can patch them.
- **Retry** (`POST /runs/{id}/retry`):
  - Allowed only from `FAILED`, `CANCELLED`, `EXPIRED` or `DUPLICATE` with `submit_attempted_at IS NULL`, or from a `COMPLETED` testing-mode run. Anything else gets 409.
  - It terminates the original workflow if it is still running, then creates a **new run** with the same posting and `origin`, `retry_of_run_id = original`, the current `submission_mode`, and a fresh alias.
  - The original row, its alias and its evidence are untouched. The response returns the new run id.
- **Cancel** (`POST /runs/{id}/cancel`): requests workflow cancellation. The workflow's cancellation path closes the browser and writes `CANCELLED`, or `SUBMIT_UNCERTAIN` if a claim exists.
- **Reconciliation:** if the workflow no longer exists, the API reconciles the row itself: `CANCELLED` if unclaimed, `SUBMIT_UNCERTAIN` if claimed.
- **One active run per posting:** a partial unique index on `application_run(job_posting_id) WHERE status IN ('PENDING','RUNNING','AWAITING_REVIEW')`.
- **Run creation** (`src/yahbah/jobs.py`, the only path that creates a run):
  - it is refused while `possibly_applied()` blocks the posting, in every mode; testing runs are ignored only as blockers;
  - if the workflow fails to start, the row is deleted rather than left `PENDING`. The row is committed before the workflow starts, so a process crash in between leaves a `PENDING` row with no workflow: it holds the posting's active-run slot until the user cancels it, which reconciles it (below).
- **Delete** is allowed only when `allowed_actions()` lists it, so in-flight and `SUBMIT_UNCERTAIN` runs can't be deleted. Bulk delete skips those runs and reports them as `skipped`.
- **Temporal end states:**
  - `COMPLETED`, `DUPLICATE`, `EXPIRED` and a review rejection complete normally;
  - `FAILED` and `SUBMIT_UNCERTAIN` fail the workflow;
  - a cancel ends it as Canceled.

## 8. Evidence and API shapes

**Fill evidence.** The `submitted_values` artifact, written by FILL, is the single per-field record for both review and post-submit display. Its rows:

```json
{"key": "…", "label": "…", "type": "text", "required": true, "options": [],
 "answer": "intended value", "source": "autofill | answer | resume | cover_letter | skip",
 "basis": ["fact ids (llm/facts.py) the answer rests on"], "generated": false,
 "written": "what code typed/selected", "page_value": "read back from the page", "ok": true}
```

**`FieldMapping`** (`src/yahbah/schemas.py`), amended 2026-10-24 for the two-pass answer engine (`llm/answers.py`):

```python
@dataclass
class FieldMapping:
    key: str
    label: str
    field_type: str
    source: str      # autofill | answer | resume | cover_letter | skip
    value: str       # the final value fill writes ("" for resume / cover_letter / skip)
    basis: list[str] = field(default_factory=list)  # fact ids (llm/facts.py) the value rests on
    generated: bool = False  # composed narrative for an open-ended free-text question, shown in review
```

- `confidence` is removed, together with `Settings.min_field_confidence`. The `source == "email"` alias override is gone: the run's alias is the value of the `profile.email` fact.
- **Invariants:**
  - every `answer` with `generated=False` has a non-empty `basis`;
  - a choice, checkbox, number, date, email, tel or url answer is never `generated`;
  - `autofill` has `basis == [fact id]`.
- **Replay safety:** nothing rebuilds `FieldMapping(**m)` from history dicts. Prior answers go to the engine as dicts, and fill reads dicts. Histories from before the cutover (with `confidence` and old `source` values) still fill and re-fill: `FillContext.value_for` returns `m["value"]` for any source other than `skip`, `resume` or `cover_letter`.

**`answer_plan` artifact** (artifact type `answer_plan`, file `answer_plan_<stage>_<attempt_tag()>.json`, metadata `{"stage", "review_round"}`, stage `map_answers | fill | map_new_fields`). It is the per-plan diagnostic record of the answer engine and replaces the `field_mappings` artifact. One is written for every plan, before a blocking plan raises `needs_answer`:

```json
{"stage": "map_answers", "review_round": null,
 "fields": [{"key", "label", "type", "required", "status": "autofill|answered|document|skipped|unresolved",
             "source", "value", "basis", "generated", "intent", "jurisdiction", "fact_ids", "reason",
             "checks": ["problem strings, in order"], "verified": true|false|null, "recovered": false,
             "repaired": false}],
 "unresolved": [{"key", "label", "type", "required", "status", "reason", "requested_info", "fact_template"}],
 "placeholders": ["fact ids"],
 "calls": [{"step", "role", "fields": <count>, "secs", "prompt_tokens", "output_tokens"}]}
```

The artifact holds fact ids, never fact values. `value` is the field's own answer, which is already in `submitted_values`. Model-written text (`intent`, `reason`, `requested_info`, verifier problems) is scrubbed of text fact values of 4+ characters before it reaches the artifact. `repaired` marks an accepted composed answer whose unsupported-number sentences code dropped. Unresolved `status` is `missing_fact | needs_clarification | unsupported | invalid_value`; required unresolved rows are what `needs_answer` blocks on, and the run page lists them with a paste-ready `fact_template`.

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
- `/stats` counts by outcome: `{"counts": {<every outcome>: n}, "total": n}`.
- List rows also carry `review_round`.

**Error bodies:** every 4xx raised by the routes has the shape `{"detail": {"code": "<snake_case>", "message": "<human text>", "run_id": "<blocking or affected run, optional>"}}`. Examples:
- `already_applied`: `POST /jobs` or `/scheduled`, with the blocking run's id;
- `not_awaiting_review`;
- `stale_review_round`;
- `action_not_allowed`;
- `invalid_url`;
- `run_in_flight`: the posting already has an active run. This comes from the §7 partial unique index, e.g. when a testing run is in flight, which `possibly_applied()` ignores.
- request and lookup errors: `invalid_run_id`, `invalid_artifact_id`, `run_not_found`, `artifact_not_found`, `missing_selection`, `invalid_request` (FastAPI validation), `temporal_unavailable` (503);
- `already_scheduled`: `POST /scheduled` for a job already in the queue;
- `scheduled_not_found` (404): an unknown scheduled-job id. A malformed id is `invalid_request`.
- settings errors: `unknown_setting` (404) and `invalid_setting` (422);
- `invalid_outcome` (422): an unknown `outcome` filter value.

**Settings:** `GET /settings` returns the **effective** values of every runtime setting in `config.RUNTIME_SETTINGS`, with `submission_mode` (`review` when missing or invalid) and `review_window_hours` (48 when missing or invalid) as runs will use them. `PUT /settings/{key}` validates through `validate_runtime_setting()` in `config.py` and rejects unknown keys.

**Artifacts:** the download route serves HTML artifacts as `text/plain` with `Content-Security-Policy: sandbox`, never as `text/html`.

## 9. Schema changes

**Migration `0018_run_lifecycle` (task 3, `down_revision = "0017"`):**
- **`application_run` additions:**
  - `submission_mode` (text, nullable; `NULL` = legacy);
  - `origin` (text, nullable);
  - `failure_reason` (text, nullable);
  - `review_deadline` (timestamptz, nullable);
  - `retry_of_run_id` (uuid, nullable, references `application_run` `ON DELETE SET NULL`).
- **Also added:** `review_round` (int, nullable), and the attempt-lease columns `lease_workflow_id` (text), `lease_activity_seq` (int) and `lease_attempt` (int), all nullable (§7).
- **Schema drift repair:** `job_posting.canonical_url` and `job_posting.location` are mapped by the ORM and present in the production DB, but no migration creates them, so a fresh `alembic upgrade head` lacks both (QA harness finding). Migration 0018 adds them (and `ix_job_posting_canonical_url`) with `IF NOT EXISTS`, and leaves them alone on downgrade. The harness's `KNOWN_DRIFT` bridge has been deleted.
- **Backfill:**
  - `submission_mode = 'testing'` where `is_test_run`;
  - `origin = 'discovered'` for runs referenced by `scheduled_job.promoted_run_id`, else `'manual'`;
  - `FAILED` with `submit_attempted_at` set becomes `SUBMIT_UNCERTAIN` (0 such rows exist today, but the rule must hold);
  - `FAILED` with `error_message = 'Cancelled by user'` becomes `CANCELLED` / `cancelled`;
  - `DUPLICATE` gets `failure_reason = 'duplicate'`; other `FAILED` rows get the contract reason their `error_message` starts with, when it is one;
  - `FAILED` with `submit_attempted_at` set gets `failure_reason = 'submit_unverified'`;
  - older in-flight runs of a posting that has a newer in-flight run become `FAILED` / `superseded` (`SUBMIT_UNCERTAIN` if attempted), so the unique index can be created. They are orphans of the in-memory Temporal dev server;
  - one `submission_claim` per posting whose run has `submit_attempted_at`.
- **Drop:** `is_test_run`.
- **New table:** `submission_claim (posting_key text PRIMARY KEY, run_id uuid NULL REFERENCES application_run ON DELETE SET NULL, claimed_at timestamptz NOT NULL)`.
- **New index:** the partial unique index from §7.
- **Foreign key:** `scheduled_job.promoted_run_id` becomes `ON DELETE SET NULL`, which unblocks deleting promoted runs.

**Migration `0019_answer_cache_provenance`** (task 5, `down_revision = "0018"`) deletes every `answer_cache` row (none has provenance), makes the primary key `(namespace, question_key)` and adds `namespace`, `basis`, `basis_hash`, `model`, `prompt_hash` and `run_id` (`ON DELETE SET NULL`); see [llm](../modules/llm.md). Integration order is task 3, then task 5.

**Migration `0020_drop_answer_cache`** (2026-10-24, `down_revision = "0019"`) drops `answer_cache`: the two-pass answer engine answers every plan from the current facts and caches nothing. The downgrade recreates the empty 0019 table (the cached rows are not restored).

## 10. Shared code location

`src/yahbah/run_state.py` holds:
- the status, `failure_reason`, `submission_mode`, `origin`, `outcome` and `Action` literals;
- the terminal-status set;
- `derive_outcome()` and `allowed_actions()`;
- `possibly_applied()` / `possibly_applied_url()`;
- `claim_submission()` and `record_possible_submission()` (§6);
- the attempt-lease helpers `activity_owner()`, `lease_clauses()`, `lease_values()` and `attempt_tag()` (§7), and `posting_key()`.

`src/yahbah/api/errors.py` holds `api_error()` / `enqueue_refused()`, which build the §8 error bodies.

The main session landed the skeleton (the literals, `derive_outcome()`, `allowed_actions()` and the `FieldMapping` fields) before wave 2; task 3 implemented the rest.

## 11. Batch-1 file ownership (historical)

How batch 1 was split; later work follows the ownership rules in the root `CLAUDE.md`. Agents edited only their own files. Each agent writes tests for its own modules under the matching `test/yahbah/<area>/` directory; the shared harness belongs to task 1.

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
