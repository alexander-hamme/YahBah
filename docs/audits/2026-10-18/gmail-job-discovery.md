# Gmail & Job Discovery Audit — gmail-job-discovery-engineer

- **Date:** 2026-10-18
- **Agent:** gmail-job-discovery-engineer (Claude Code development agent)
- **Revision examined:** branch `browser-use` @ `2911263`, plus uncommitted `CLAUDE.md` edit (no source changes)
- **Scope:** Gmail client and both pollers, alert-email extraction, URL normalization and resolution, scheduled-queue admission and promotion (`scheduled_job`, `jobs.py`, `/scheduled`), per-run aliases, verification-code retrieval, and status-email attribution.
- **Status:** Historical point-in-time audit. Findings may become outdated; the wiki and source are authoritative.

## Summary
The discovery → admission → scheduled-queue pipeline runs end to end, but its only real use failed completely. All 302 jobs ever scheduled came from jobright.ai alert emails. All 301 that were promoted became runs that FAILED at discovery within a minute. These runs were started in a single 24-second burst on 2026-06-15, 13 days after their promote time.

The causes are structural:
- Links are filtered by regex before they are resolved.
- Aggregator pages are never resolved to the employer's application.
- URL normalization removes real job identity (`gh_jid`) but keeps source tracking parameters (`imp_id`).
- The admission gate is open. The threshold is 0, unscored jobs are admitted, promotion goes straight into submitting workflows, and the startup cycles ignore the "disabled" toggles.

Ingestion also moves its checkpoint past messages that failed, and the scheduled queue has states that never resolve and decisions that do not stick.

The Gmail client is a reasonable base: OAuth, paginated history, alias generation and labels. Safety and job identity should be fixed before building company/title dedup, per-company caps or push sync on top of it. None of this code has tests.

## Findings

### F1. Discovery can start unreviewed, submitting applications, and the "disabled" toggles do not stop it
- **Priority:** Critical
- **Confidence:** Confirmed defect (code trace; DB confirms the burst behaviour). Not verified: whether the new agentic discover would get from a jobright page to a submittable form.
- **Issue or opportunity:**
  1. The worker-start cycle ignores `job_alert_polling_enabled`. `run_alert_poller` calls `poll_for_alert_emails` and `_promote_ready_jobs` unconditionally at startup (`src/yahbah/gmail/alert_poller.py:61-73`); the flag is checked only inside the loop (`:78`). Every worker start therefore:
     - reads alert mail;
     - labels and archives it (`job_alert_auto_archive: true`, `:351-356`);
     - starts workflows for every ready SCHEDULED row.
  2. Admission is open. `job_alert_min_match_score: 0` means "0 = accept all" (`src/yahbah/config.py:71`). So the answer to "is 0 admit-everything?" is yes, by design as a mode, and it is the shipped value. Even with a threshold set, a missing score is admitted (`alert_poller.py:317` only skips when `match_score is not None`), and `score_job` turns every LLM failure into `(None, None)` (`alert_parser.py:306-308`). Admitted scores observed: 28–92, mean 73.
  3. Promotion goes straight into the normal submit path. `_promote_ready_jobs` → `submit_job_for_application(url)` (`src/yahbah/jobs.py`) creates an ordinary run. The only submit gate is the global `testing_mode`, currently `false`. There is also no:
     - per-origin policy;
     - age check (`EXPIRED` is in the model comment and API filters but nothing ever sets it);
     - per-cycle cap;
     - check on where the browser is being sent (no destination validation anywhere in `src/yahbah`).
  4. The inputs are untrusted.
     - Senders are matched on the From header only (`alert_parser.py:55-67`), with no check of `Authentication-Results`/DKIM.
     - `_is_job_url` accepts any scheme or host whose path contains `/(jobs|careers|positions|apply)/`. Probe: `http://127.0.0.1:8000/jobs/…`, `http://192.168.1.1/apply/…` and `file:///…/jobs/…` are all classed `generic` and kept by `extract_job_urls`.
     - `normalize_job_url` and `fetch_job_description` then send HEAD/GET requests to those URLs, following redirects, with no response-size cap (`url_utils.py:147-156`, `alert_parser.py:254-262`).
- **Evidence:** Read-only DB aggregates:
  - The 301 promoted runs were created 2026-06-15 16:23:23–16:23:47. All were 10–20 days past `promote_after`, for rows ingested 14 days earlier. The promoter did not run in between, then promoted the whole backlog at once.
  - Both `gmail_checkpoint` rows last advanced on 2026-06-16.
  - `config/settings.yaml` has `job_alert_backfill_since: '2026-04-16'`, 7 sender patterns, and `testing_mode: false`.
- **Impact:** With the code as it is, the next worker start will do one of two things:
  - process about 4 months of mailbox history; or
  - if the history id has expired (Gmail says ids are typically valid for at least a week), re-search alerts since 2026-04-16.

  Either way it archives those emails, scores every linked job, and schedules them all. The first worker start 24 h or more later then promotes them in one burst, into runs that submit whenever the agent reaches a form. Any link in an email that passes the sender check can receive the applicant's personal data and résumé, with no human review. This breaks the rule that discovery must not authorize submission on its own.
- **Proposed resolution:**
  - Now: gate the startup alert cycle and promotion on the enabled flag, or clear `job_alert_sender_patterns`, before the next worker start.
  - Record an explicit admission outcome per job: `ADMITTED | BELOW_THRESHOLD | SCORE_FAILED | UNRESOLVED | NOT_A_JOB | STALE`. Only `ADMITTED` can be promoted. A failed score is retried, never admitted.
  - Add an origin/submit policy to `ApplicationWorkflowInput`, so discovered jobs default to fill-only or pre-submit review until the user opts in. This fits the planned review stage.
  - At promotion: expire jobs older than a configurable age, and cap promotions per cycle.
  - URL policy at intake: http(s) only, public routable hosts only. Resolve the host and reject loopback, private and link-local addresses, re-check on every redirect hop, and cap response size.
- **Failure scenario & validating test:**
  - Fake `GmailClient`, settings `job_alert_polling_enabled: false`. Starting `run_alert_poller` must call neither `_modify_labels` nor `submit_job_for_application`.
  - `score_job` raises → the row is not promotable.
  - Table test: the URL policy rejects loopback, RFC 1918 and `file:` URLs, and redirects into them.

### F2. Link extraction, URL resolution and job identity are not fit for purpose: 0 of 301 promoted jobs reached a form
- **Priority:** High
- **Confidence:** Confirmed defect (offline probes on synthetic inputs, plus DB). Not verified: the specific jobright → employer resolution path.
- **Issue or opportunity:**
  - **`gh_jid` is stripped as if it were tracking.** It is in `_TRACKING_PARAMS` (`src/yahbah/url_utils.py:33`), but it is the Greenhouse job id on company-hosted career pages. The agent layer depends on it: the `[?&]gh_jid=\d+` URL pattern (`src/yahbah/agent/boards.py:69`) and the Greenhouse model note (`boards.py:93-94`).
    - Probe: `https://www.example-co.com/careers/open-roles?gh_jid=111` and `…?gh_jid=222` both normalize to `…/careers/open-roles`, and `GREENHOUSE.matches_url()` then returns False.
    - This runs on every intake path: `POST /jobs` (`api/routes/jobs.py:28`), `POST /scheduled`, and alert extraction. The same `strip_tracking` also builds `canonical_url` in `discover_activity` (`workflows/activities/browser.py:194`).
  - **Aggregator tracking is kept.** jobright's `imp_id` is not stripped: 300 of 302 scheduled URLs carry it. The 302 rows are only 268 distinct jobs (34 duplicate rows), and 33 jobs got two runs each. In addition, `parse_qs` without `keep_blank_values` silently drops blank parameters.
  - **Links are matched by regex before resolution.** `_is_job_url` runs on the raw href (`alert_parser.py:147-151`), and only matching links are resolved. Probes:
    - ESP click-trackers (SendGrid `/ls/click`, Mailchimp `list-manage.com/track/click`, generic `click.*` wrappers) return None, so tracked links are dropped without being resolved.
    - Direct Lever, Ashby and Workday posting URLs return None. Workday is the named next target.
    - `_SKIP_PATTERNS` matches substrings anywhere in the URL (`:90-95`), so `/careers/jobs/help-desk-technician-123`, `/jobs/settings-engineer-456` and `/jobs/privacy-counsel-789` are dropped.
    - Meanwhile `jobright.ai/jobs/recommend?login=…`, which is not a job, was admitted (2 rows in the DB).
  - **Aggregator pages are never resolved.** `normalize_job_url` makes one HEAD request (`url_utils.py:153`), and jobright `/jobs/info/<id>` is a 200 aggregator page. The archived page in `artifacts/7ec683a6-b71b-415b-a7bd-802791b91352/trace.zip` has:
    - JSON-LD `JobPosting` (title, hiringOrganization, jobLocation, datePosted, validThrough);
    - `rel=canonical`;
    - no employer apply URL in its server-rendered data, only an `isCompanySiteLink` flag. The "Apply on Employer Site" target needs a click or JS, and possibly a login.

    `BoardProfile.resolve_application_url` exists for exactly this case, but no profile sets it (`boards.py:41-43`).
  - **Metadata is thrown away.** `title`, `company` and `location` are hard-coded to `None`, "populated later" (`alert_poller.py:330-332`), and nothing ever fills them: 302/302 are NULL even though JSON-LD has them. `source` is `"other"` for all 302 rows, because `_DOMAIN_TO_SOURCE` has no jobright entry.
- **Evidence:**
  - DB: all 302 `scheduled_job` rows are on host `jobright.ai`. 301/301 promoted runs FAILED with "Page does not appear to be a job application form" (296 in under a minute, 5 in about a minute). These were on the old pipeline; the agentic discover has not been tried on jobright.
  - History: all 10 company-hosted (non-Greenhouse-host) runs used `?gh_jid=` URLs, and 7 COMPLETED. In 2 of them the job id appears only in `gh_jid`. All predate commit `cf9cfcc` (2026-04-20), which added `gh_jid` to the blocklist.
- **Impact:**
  - Discovery has produced no usable jobs.
  - With current code, a manually entered company-site URL opens the generic careers page, so discover fails or picks the wrong listing.
  - Different jobs collapse into one `job_posting.url` row (the column is unique). After one is applied, canonical check A in `check_duplicate_activity` marks every other job on that page DUPLICATE.
  - Cross-source "already applied" checks cannot work, because aggregator URLs never equal ATS URLs.
  - Planned company/title dedup and per-company caps have no data to work from.
- **Proposed resolution:**
  1. Quick fix: keep `gh_jid` and strip `imp_id`.
  2. Replace the single global blocklist with an identity-preserving normalizer driven by per-board rules. Board-specific rules belong in `agent/boards.py` per CLAUDE.md. For unknown hosts, strip only well-known global trackers (`utm_*`, `gclid`, `fbclid`, …).
  3. Store a structured identity key `(ats, tenant, job_id)`. `credentials._extract_job_parts` already derives it for alias hashing. Dedup on that key.
  4. Add a resolution stage between extraction and admission that returns a typed result: `source_url`, `resolved_url`, `posting_url`, `application_url`, identity key, JSON-LD metadata with provenance, and an outcome enum. Steps:
     - collect every href (and plaintext URLs);
     - drop non-jobs by path segment, not substring;
     - GET with the F1 URL policy checked on each hop;
     - parse JSON-LD, canonical link and ATS patterns;
     - for aggregators, try HTTP first, then a bounded browser hand-off via `resolve_application_url`;
     - otherwise mark `UNRESOLVED` (visible for review, never auto-promoted).
- **Failure scenario & validating test:**
  - Table-driven normalizer test: two `gh_jid` jobs stay distinct; `imp_id` is stripped; LinkedIn `/comm/` maps to `/jobs/view/<id>`; `normalize(normalize(u)) == normalize(u)`.
  - Extraction tests per source on sanitized alert-email fixtures (jobright digest, LinkedIn, Indeed, ESP-wrapped), measuring yield and precision.
  - Resolver tests on a mocked transport: redirect chain, meta-refresh, JSON-LD, unresolvable aggregator → `UNRESOLVED`.

### F3. The scheduled queue has states that never promote, decisions that don't stick, and promotion that isn't idempotent
- **Priority:** High
- **Confidence:** Confirmed defect (code trace; DB confirms the hand-off and timing behaviour)
- **Issue or opportunity:**
  - **APPROVED is a dead end.** `approve` sets `APPROVED` and is documented as "still waits for promote_after" (`src/yahbah/api/routes/scheduled.py:6, 240-254`), and the UI offers it. But the promoter selects only `status == "SCHEDULED"` (`alert_poller.py:377-383`), so approved jobs never auto-promote. Approving a SKIPPED row strands it the same way.
  - **Rejection does not stick.**
    - `reject` sets `SKIPPED` (`scheduled.py:268`), and both dedup queries exclude SKIPPED (`alert_poller.py:283`, `scheduled.py:116`). The next alert that contains the same job re-inserts it as SCHEDULED, re-fetched and re-scored.
    - Low-score auto-skips are re-scored every time they reappear, and the non-deterministic score can flip them.
    - FAILED runs don't count as "applied" (`alert_poller.py:298`), so a job that keeps failing is re-queued from every alert. This is the likely source of the 33 jobs with repeat runs.
  - **Promotion is neither atomic nor idempotent.** `submit_job_for_application` commits the run (`jobs.py:64`) before `start_workflow` (`:67`). The scheduled row is marked PROMOTED in a separate transaction (`alert_poller.py:397-401`, `scheduled.py:305-313`). As a result:
    - a Temporal error leaves an orphan PENDING run, and because the row stays SCHEDULED, every later cycle adds another;
    - a crash after the workflow starts leads to a second promotion;
    - the API `/promote` and the background promoter can promote the same row, because nothing claims it conditionally.
  - **Promotion runs only inside the alert-poll loop.** When polling is disabled, manual `POST /scheduled` additions are promoted only when the worker restarts, all at once (the F1 burst).
  - **The hand-off drops computed data.** Only the URL is passed: 0 of 301 promoted runs carry the admission score. The run recomputes the score from different inputs:
    - profile: `alert_parser.build_profile_summary` reads personal.yaml, while `workflows/activities/llm.py:86-104` reads the DB profile;
    - description: the HTTP page text at admission versus the discovered description in the run.
  - **Nothing stops duplicate rows.** Migration `0016` has no unique constraint for active `job_url` rows, and the API's manual triggers can run concurrently with the worker (F4).
- **Evidence:**
  - Code references above.
  - DB: no promoted run has a `match_score`. Promotions were 10–20 days late and happened in a 24 s burst. 33 job paths have 2 runs each.
- **Impact:**
  - Approve and Reject don't do what the UI says.
  - Transient failures produce duplicate and orphan runs.
  - The planned "max-K per company", "auto-excluded archive" and "reuse LLM results" features have no stable foundation.
- **Proposed resolution:** One explicit state machine:
  - SCHEDULED/APPROVED → PROMOTING → PROMOTED.
  - User `REJECTED` kept distinct from auto `SKIPPED`. Both block re-ingestion of the same identity key unless re-evaluation is requested.
  - `EXPIRED` assigned by age.

  Promotion:
  - Claim the row with `UPDATE … SET status='PROMOTING' WHERE id=:id AND status IN ('SCHEDULED','APPROVED') AND NOT hold RETURNING`.
  - Derive the workflow id from the scheduled id, so a retried start is rejected as a duplicate.
  - Commit the run and the PROMOTED status together, start the workflow after commit, and reconcile orphans.

  Also:
  - Run the promoter as its own loop, independent of alert polling.
  - Pass `scheduled_job_id`, score and description into the run.
  - Add a partial unique index on the identity key for non-terminal rows.
- **Failure scenario & validating test:**
  - Transition-table unit tests, including APPROVED + elapsed `promote_after` → promoted.
  - `start_workflow` raises once → after a retry there is exactly one run and no orphan.
  - Reject → the same job arrives in a new alert → not re-scheduled.
  - Concurrent API promote + background promote → one run.

### F4. Ingestion silently loses messages, and the status poller runs the LLM on every email in the mailbox
- **Priority:** High
- **Confidence:** Confirmed defect for checkpoint and truncation behaviour (code trace). How many messages have actually been lost is not measured.
- **Issue or opportunity:**
  - **The checkpoint moves past failures.** Per-message exceptions are logged, counted as "skipped", and the checkpoint still advances to the new history id (`alert_poller.py:211-217`, `poller.py:160-166`). That message is gone for good if it hits any of:
    - a Gmail 5xx;
    - a DB error;
    - an Ollama failure. Status classification and alert scoring are LLM calls; `ollama_timeout` is 900 s and Ollama is shared with live runs.
  - **First run writes the checkpoint before processing the backfill** (`alert_poller.py:156-162`, `poller.py:115-123`). A crash mid-backfill loses the rest.
  - **Fallback and backfill searches are single-page.**
    - Limits: 200 (`alert_poller.py:454`) and 100 per query (`poller.py:222`), and neither follows `nextPageToken`.
    - With `job_alert_backfill_since` unset, the alert fallback returns `[]` (`:427-428`), silently skipping everything since the expired checkpoint.
    - Expiry detection relies only on the "404" text test, because `"historyId" in str(exc).lower()` can never be true (`client.py:256`).
  - **Concurrent cycles.** `POST /gmail/poll`, `/gmail/alert-poll` and `/gmail/alert-backfill` (`api/main.py:64-97`) run full cycles in the API process while the worker's pollers also run, with no single-flight lock. `trigger_backfill` deletes the checkpoint row while a cycle may be running; `_update_checkpoint`'s `scalar_one()` then raises, or two re-inserts collide on the unique key.
  - **Status-poller scope.** The wiki and README say it "keeps only +alias recipients". In fact every message added to the mailbox that has a body is fully fetched and classified by the driver model (`poller.py:256-300`), then attributed by company name if no alias matches (F5). That means:
    - one local LLM call per personal email, competing with live runs, and each one a chance to lose the message;
    - history `labelIds` (SENT/DRAFT) are not used to pre-filter;
    - messages are fetched with `format=full` just to read headers.
  - **Health is invisible.** Errors go only to logs. There is no last-success time, lag, or auth state anywhere.
- **Evidence:** Code references above. DB: both checkpoints last advanced 2026-06-16. No tests cover any of this.
- **Impact:** Interview invitations, rejections and alerts can be missed with no signal, and manual triggers can produce duplicate processing. It also blocks the planned push sync, which needs exactly this durability.
- **Proposed resolution:**
  - Add a durable Gmail inbox. Page through history (or search) and insert message ids into `gmail_inbox(poller, message_id, status, attempts, last_error)` in the same transaction that advances the checkpoint. Process from the inbox with bounded retries, ending each message as `DONE`, `IGNORED` or a visible `FAILED`.
  - Paginate all searches.
  - Take a Postgres advisory lock per poller key, and make the API triggers only wake the worker loop.
  - Pre-filter on history `labelIds` plus `format=metadata` (From/To/Cc/Delivered-To). Send only alias-addressed or allowlisted-sender messages to the LLM.
  - Expose poller health via the API: last success, oldest pending, last error class, auth failure.
- **Failure scenario & validating test:**
  - Fake Gmail where one message raises → the checkpoint advances but the message stays pending and succeeds on the next cycle.
  - 3-page history response → all messages ingested.
  - Expired history → the fallback search pages through all results.
  - Two concurrent cycles → no duplicate `scheduled_job` rows.

### F5. Email verification-code extraction accepts ordinary words, and status emails can be attached to the wrong application
- **Priority:** Medium
- **Confidence:** Confirmed defect for the code heuristic (probe). Strongly supported risk for misattribution: the aggregates cannot verify it.
- **Issue or opportunity:**
  - **Code heuristic.** `extract_code_heuristic` returns the first `<h1>` text that matches `[A-Za-z0-9]{4,12}` (`src/yahbah/gmail/parser.py:82-95`).
    - Probe: "Thanks", "Welcome" and "Confirmation" are all returned as codes. So is "Acme", even when the body says the code is 739201.
    - Since the revamp, the subject filter comes from `BoardProfile.email_code_subject`, which only Greenhouse sets (`boards.py:58,81`). On every other board the query is just `to:<alias> after:<submit−90s>` (`client.py:131-136`). The "application received" email that often arrives alongside the code is therefore a candidate, and newest-first ordering (`:154-157`) can pick it.
    - The wrong "code" is typed. The message is labeled Parsed and, with `gmail_verification_code_action: archive`, archived (moved to trash with `delete`) before the page has accepted it.
    - On `main` the subject was hard-coded to "security code", so this is a regression from the revamp on generic boards.
    - Codes are logged at INFO (`client.py:212`).
  - **Status attribution.** When the `To` alias doesn't match, `_match_by_company` attaches the email to the newest COMPLETED or FAILED run whose company matches `ILIKE '%<llm company>%'` (`poller.py:372-389`):
    - the substring is unescaped, so short names match many companies;
    - runs that never submitted are eligible;
    - several applications at one company resolve to the newest;
    - `confidence` is ignored.

    Only `To` is checked (`:267-277`), so an alias delivered via Cc or Delivered-To falls through to this fuzzy path. Separately, `generate_email_alias` reuses an existing alias after 5 hash collisions (`credentials.py:85`), which F3's re-queuing makes reachable. `_match_by_alias` (`scalar_one_or_none`) then raises and the message is dropped (F4).
- **Evidence:**
  - Probe output (synthetic HTML).
  - `git diff browser-use main -- src/yahbah/gmail/client.py` shows the subject filter was removed.
  - DB: 28 status updates, 11 from non-Greenhouse senders, and 6 runs with more than one status type; attribution correctness cannot be checked from aggregates.
- **Impact:**
  - Email-code flows on non-Greenhouse sites can fail, or consume and hide the wrong email.
  - Rejections and interview invitations can be shown on the wrong application.
- **Proposed resolution:**
  - Code extraction:
    - accept a code only if it contains a digit or matches the board's declared code shape, and appears near code keywords;
    - default generic subject/body filter: code, verification, verify, security, one-time;
    - run the LLM fallback only on such emails;
    - change mailbox state only after the page accepts the code (a callback from `submit.py`).
  - Status attribution:
    - auto-attach only on an alias match (To/Cc/Delivered-To/X-Original-To), or when exactly one submitted run (`submit_attempted_at` not null) matches the employer domain or normalized company name;
    - otherwise store as "unmatched — needs review" with the candidate runs listed;
    - make alias generation fail loudly instead of reusing an alias.
- **Failure scenario & validating test:**
  - Table of negatives and positives for the code heuristic.
  - Confirmation email and code email arrive together → the code is chosen.
  - Two COMPLETED runs at the same company → the email is left unmatched, not given to the newest.
  - Alias only in Cc → matched by alias.

## Documentation discrepancies
- `docs/wiki/modules/gmail.md`, `README.md:176` and the `poller.py` docstring say the status poller keeps only `+alias` recipients. The code classifies every message and falls back to company-name matching (`poller.py:273-307`).
- `README.md:178` ("Polling loops are off by default") and `gmail.md` describe the unconditional startup cycle only for the status poller. The alert poller's startup cycle also ignores its flag: it ingests and archives mail and promotes ready jobs (`alert_poller.py:61-73`).
- `gmail.md` says jobs are promoted after `job_alert_promotion_delay_hours`. In fact only SCHEDULED rows are, never APPROVED, and only from the alert loop or at startup.
- `docs/wiki/modules/api-frontend.md` and the README route table omit `POST /gmail/alert-backfill` (`api/main.py:89`).

## Cross-area notes
- [llm-intelligence-engineer] The match score is computed twice, on different inputs: personal.yaml plus HTTP page text at admission, versus the DB profile plus the discovered description in the run. It is also clamped to 22–99 (`field_mapper.py:382`). Defining one scoring input contract would make admission and run scores comparable and reusable.
- [browser-navigation-engineer] Getting from an aggregator to the employer site (jobright's "Apply on Employer Site"; the unused `resolve_application_url` hook) needs a bounded browser task that discovery can call. Separately, promoted URLs reach the browser with no scheme or host validation (loopback, LAN, `file:`), so the URL policy should be shared.
- [temporal-platform-engineer] The pollers and the promoter are bare asyncio tasks in the worker, with no lock shared with the API's manual triggers. Idempotent promotion needs deterministic workflow ids plus a reconciler for orphan PENDING runs (`jobs.py:64-67` commits before `start_workflow`). The durable Gmail inbox table needs a migration.

## Proposed action plan
1. **Close the gate (F1):** gate the startup alert cycle and promotion on the enabled flag before the next worker start. Never admit unscored jobs. Default discovered runs to no-submit via an origin/submit policy in the workflow input. Add expiry, a per-cycle cap and the URL scheme/host policy. Intended outcome: no email can cause a submission without an explicit user policy.
2. **Job identity and resolution (F2):** keep `gh_jid` and strip `imp_id` now (quick fix). Then build a per-board normalizer with an `(ats, tenant, job_id)` identity key, and a resolver stage with typed outcomes and JSON-LD metadata, tested against sanitized alert fixtures. Intended outcome: discovered jobs resolve to employer/ATS postings with stable identities that dedup across sources.
3. **Scheduled-queue state machine and idempotent promotion (F3):** with temporal-platform. Intended outcome: Approve and Reject behave as shown, promotion runs exactly once, and admission data carries into the run.
4. **Durable Gmail inbox, poller health, and verification/attribution hardening (F4, F5):** includes the regression tests listed under each finding. Intended outcome: no silent message loss, and email-code and status flows that are safe on non-Greenhouse boards.
- **Dependencies / collaboration:**
  - browser-navigation: aggregator resolve hook; shared URL policy.
  - temporal-platform: workflow ids, locking, inbox and identity-key migrations.
  - llm-intelligence: one scoring contract; status-classification confidence.
  - application-forms: code-accepted callback before mailbox mutation.
  - product-dashboard: REJECTED vs SKIPPED, `UNRESOLVED`/unmatched review views, poller health.
  - browser-agent-qa: alert-email and aggregator fixtures.
- **Defer:**
  - Gmail push (watch/Pub/Sub). Polling is adequate at this volume, and push needs the same durable inbox first.
  - LLM-based dedup and natural-language blacklists, until identity keys exist.
  - Querying job boards directly.
  - Multi-account.
