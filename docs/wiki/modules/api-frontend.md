# API and Frontend

## FastAPI (`src/yahbah/api/main.py`, routers in `api/routes/`)
Prefixes:
- `/applications`: list and `/stats` (below).
- `/jobs`: `POST` enqueues (201).
- `/runs`: detail, review, steps, artifacts, status updates, the review/cancel/retry/resolution actions, bulk and single delete. The `runs.py` module docstring lists every route. `/steps` returns nothing, since no code writes `application_step` rows. `/artifacts` lists only files recorded in `application_artifact`; `trace.jsonl` and the `llm/` logs are on disk only.
- `/scheduled`: create, list, `/stats`, get, `approve`/`reject`/`hold`/`promote`, delete.

`main.py` also serves `GET /health`, the manual poller triggers `POST /gmail/poll`, `/gmail/alert-poll` and `/gmail/alert-backfill`, and the runtime settings. Response models are Pydantic classes in the route files.

Run: `uv run uvicorn yahbah.api.main:app --reload --port 8000` (binds 127.0.0.1).

**Run lifecycle on the wire** ([contract](../contracts/run-lifecycle.md) §8):
- **Derived on the server.** Run detail and list rows carry `outcome` and `actions`, from `run_state.derive_outcome()` and `allowed_actions()`. They also carry `failure_reason`, `submission_mode`, `origin`, `submit_attempted_at`, `review_deadline`, `review_round` and `retry_of_run_id`. `is_test_run` is gone.
- **`GET /applications`** takes:
  - a multi-value `outcome` filter, repeated (`?outcome=a&outcome=b`) or comma-separated; an unknown value gets 422 `invalid_outcome`;
  - `search`;
  - `sort`, where `review_deadline_asc` puts the soonest deadline first and undated rows last.

  `status` and `exclude_status` were removed.
- **Outcome in SQL.** The outcome filter is a SQL `CASE` built by evaluating `derive_outcome()` over every status × mode class × confirmed combination (`OUTCOME_SQL` in `applications.py`), so SQL and Python can't disagree.
- **`GET /applications/stats`** returns `{"counts": {<every outcome>: n}, "total": n}`.
- **Errors.** Every 4xx raised by a route has the body `{"detail": {"code", "message", "run_id"?}}` (build them with `api/errors.py`'s `api_error()`). FastAPI's own request-validation failures get the same shape from a handler in `main.py`: 422 `invalid_request`, with the errors summarized in `message`. The harness's bare `make_app()` doesn't install that handler.

**Runtime settings** (`config/settings.yaml`):
- **`GET /settings`** returns the *effective* values (`config.get_effective_runtime_settings()`): `submission_mode` is `review` when missing or invalid, and `review_window_hours` is 48 when missing or invalid. Only keys in `config.RUNTIME_SETTINGS` are returned, so the legacy `testing_mode` no longer appears.
- **`PUT /settings/{key}`** takes `{"value": …}`:
  - an unknown key gets 404 `unknown_setting`;
  - the value is validated by `config.validate_runtime_setting()`, and an invalid one gets 422 `invalid_setting`;
  - on success it returns the effective settings.
- Runs snapshot `submission_mode` and `review_window_hours` at creation, so a change affects new runs only.

**Local-only, no auth.** `main.py` rejects any request whose `Host`, or `X-Forwarded-Host` when present, is not `localhost`/`127.0.0.1` (400; blunts DNS rebinding). The Next `/api` rewrite sends `Host: localhost:8000` and the browser's host in `X-Forwarded-Host`, so both are checked. IPv6 literals (`[::1]`) are not accepted. API test clients must use `base_url="http://localhost"`. Do not expose either port beyond loopback without adding auth first.

## Frontend (`frontend/`, Next 16.2.1 / React 19)
`frontend/AGENTS.md` warns this Next version has breaking changes: read `node_modules/next/dist/docs/` before writing Next code. Dev: `npm run dev` in `frontend/` (`next dev -H 127.0.0.1`; `start` is bound the same way, so the dashboard and its `/api` proxy are not reachable from the LAN). All pages are client components on react-query, polling every 10–30 s.

**Pages** (`src/app`):

| Route | What it shows |
|---|---|
| `/applications` | The attention strip: uncertain runs to resolve, and the review count with the next deadline. Counts by outcome. The job form. The outcome filter (in the URL as `?filter=`: a single outcome or `hide_failed`/`all`/`needs_you`/`sent`). The list. |
| `/applications/[id]` | Detail. It shows the review panel when the run lists `approve`/`reject`, the uncertain-run resolution panel (`#resolve`) when it lists `confirm_*`, then info, fill evidence and screenshots. |
| `/review` | The review queue: `awaiting_review` runs, soonest deadline first, each with a countdown (and "Round N · new fields" after the first round), beside the selected run's review panel (`?run=`). After a decision it moves to the next run. By default the selected run opens in the compact **triage preview** (see Triage below); the full review is one step away. |
| `/scheduled` | The scheduled job queue. |

**Rule: the UI never derives run state.**
- Badges render `outcome` (`OutcomeBadge`, copy in `lib/outcomes.ts`).
- Buttons appear only for the run's `actions` (`RunActions`):
  - in list rows, approve/reject become a **Review** link, and the `confirm_*` actions a **Resolve** link;
  - retry confirms with the mode the new run gets, then navigates to the new run id;
  - delete and cancel confirm inline.
- `failure_reason` is shown in words (`lib/failure-reasons.ts`, with a fallback for unknown codes).
- `test/yahbah/api/test_frontend_contract.py` fails when the TypeScript unions in `lib/types.ts` (`Outcome`, `RunAction`, `FailureReason`, `SubmissionMode`, `Origin`) drift from `run_state.py`. A batch-2 `auto_reviewed` mode therefore also needs `MODE_OPTIONS` in `submission-mode-control.tsx`.

**Review** (`components/review/`):
- `ReviewPanel` renders `GET /runs/{id}/review`:
  - **answers** (`AnswerEvidenceList`): each row shows its question, the answer, the `basis` keys or a **Generated** label, and what the page read back (`page_value`), with `ok=false` flagged;
  - **flags**: rows to check are flagged (mismatch, generated, factual without basis, required and blank, new), and the summary can narrow the list to them;
  - **`new_fields`**: shown in their own section;
  - the full-page screenshot (scrollable, with a lightbox) and the cover letter.
- `DecisionBar` approves or rejects exactly `review_round`:
  - approve is enabled only once the box "These answers are right: submit this application to …" is ticked;
  - reject takes an optional reason;
  - a 409 `stale_review_round`, `not_awaiting_review` or `action_not_allowed` refetches the review and explains; the box resets, because the bar is keyed by the round.

**Triage** (`/review`; added 2026-10-20). Quick approve/reject from the queue: eyeball a run, decide, open the full review only for the unsure ones. The rules (no bulk route, round from the rendered payload, dwell, undo window, visible drops) are in [run-lifecycle §4, "Triage"](../contracts/run-lifecycle.md); this section is the implementation.
- **Preview** (`triage-preview.tsx`): a compact view of `GET /runs/{id}/review` for the selected run. It shows every flagged answer (the same flags as the full review, including the text of generated answers). **O** or the button opens the full `ReviewPanel` with its tick box.
- **Keys** (ignored in inputs and with modifiers, via `isShortcutBlocked`): `j`/`k` next/previous run (arrows too, from the queue list); `y` approve; `n` reject; `o` (or `Enter`, unless focus is on a button or link) open the full review; `u` or `Esc` undo the newest pending decision. A held `y`/`n` (`event.repeat`) never decides. `y`/`n` act only once the preview has been on screen for `DWELL_MS` (800 ms) at its current round.
- **Decisions** (`use-triage-decisions.ts`): each one is a single `approveRun`/`rejectRun` call carrying the round of the payload the preview rendered. It waits `UNDO_MS` (5 s) in an undo bar, for reject as well as approve. Before sending, `pendingDropReason` re-checks the query cache; a decision is dropped (and reported) if the round changed, the run left the queue or the payload is stale. Hiding the tab or leaving the page cancels pending decisions (`left_page`).
- **Problems are shown, not swallowed** (`TriageProblem`): `dropped`, `conflict` (409 codes, refetch), `error` (4xx, nothing recorded) and `unknown` (no response or 5xx: the decision may have been recorded). Notes outlive the row they concern (`TriageNote`).
- **Tests:** `review-triage.test.tsx` and `review-triage.qa.test.tsx` (the latter is the adversarial QA suite).

**Mode control:**
- `SubmissionModeControl` is a radiogroup in the header labelled "New runs" (Testing | Review).
- It shows **Mode unknown**, never a mode, when settings fail to load.
- After a change it toasts that started runs keep their mode.

**Errors:**
- `lib/api.ts` throws `ApiError` (`status`, `code`, `message`, `runId`), parsed from the §8 body. Other bodies get `http_<status>`; no response gets `network_error`.
- Every mutation error is toasted by the `MutationCache` in `providers.tsx` (`lib/toast.ts` store, `<Toaster>`), unless the mutation sets `meta.inlineErrors` and renders `ApiErrorMessage` itself. The job form, the review decision, the resolution panel and the scheduled add form do this.
- `already_applied` and `run_in_flight` errors link to `detail.run_id`.
- A failed query shows an inline error and keeps the last data.

**Frontend checks** (`frontend/`, run from there; state on 2026-10-20):
- `npm test`: Vitest 5 with React Testing Library in jsdom, setup `vitest.setup.ts`, tests colocated as `src/**/*.test.ts(x)` (13 files, 112 tests, passing). `npm run test:watch` for watch mode.
  - **Helpers:** `src/test/render.tsx` provides `renderWithClient` (the app's real QueryClient setup, no retries) and `mockApi` (stubs `fetch` per `"METHOD /path"`, records calls). `src/test/fixtures.ts` builds §8-shaped runs, rows and reviews.
  - **Navigation:** `vitest.setup.ts` mocks `next/navigation` with `src/test/navigation.ts`, since the app router isn't mounted in jsdom.
  - **Clean-up:** the setup resets toasts and stubbed globals after each test.
- `npm run lint`: ESLint 9 flat config (`eslint.config.mjs`, extends `eslint-config-next` core-web-vitals and typescript). Clean. ESLint 10 is not usable yet: the plugins bundled with `eslint-config-next` stop at ESLint 9.
- `npx tsc --noEmit`: type check (clean). `npx next build` also passes. Pages that read search params wrap the component in `<Suspense>`, which prerendering requires.
- `node scripts/check-untrusted-html-sandbox.mjs`: a real-browser check that `UntrustedHtmlFrame` runs no script and makes no network request, compared with an unsandboxed control frame that must do both. Needs local Chrome or Chromium (`CHROME_PATH` overrides). Exit 0 pass, 1 fail, 2 inconclusive or no browser. jsdom cannot test this, so run it after changing `lib/untrusted-html.ts` or the frame.

**Untrusted HTML:** never render job-site or email HTML with `dangerouslySetInnerHTML`. Use `UntrustedHtmlFrame` (`components/untrusted-html-frame.tsx`). It is an `<iframe sandbox="" srcDoc>` (no scripts, opaque origin), and `lib/untrusted-html.ts` prepends a CSP that blocks all network loads. `RunInfoCard` uses it for `confirmation_html`. Job URLs in the review panel become links only if they are `http(s)` (`httpUrl()`).
