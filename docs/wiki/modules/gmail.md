# Gmail Integration (`src/yahbah/gmail/`)

All blocking Google API calls are wrapped in `asyncio.to_thread`. OAuth setup: credentials in `settings.gmail_credentials_dir` (default `~/.config/yahbah/gmail`); one-time consent via `uv run python -m yahbah.gmail.client`.

- `client.py` `GmailClient`: search, history list, labels, archive, verification-code retrieval:
  - `wait_for_verification_code(alias, since_epoch, subject_hint, ..., code_pattern=None, exclude_message_ids=())` -> `VerificationCode(code, message_id)`. Polls `to:<alias> [subject:(<hint>)] after:<submit-90s> -label:<Parsed>` and **changes nothing in the mailbox**. `subject_hint` is the board's `email_code_subject`; `code_pattern` is the board's `email_code_pattern`, a regex the whole code must match (Greenhouse: `[A-Za-z0-9]{8}`; `agent/submit.py` passes both); `exclude_message_ids` skips, for example, a message whose code the page rejected.
  - `finalize_code_message(message_id)`: applies `gmail_verification_code_action` (`archive`: label Parsed and move to the YahBah folder; `delete`: trash; `nothing`: label Parsed). Call it once the page has accepted the code.
  - `fetch_verification_code(...)` -> `str`: wait, then finalize straight away, before the page has seen the code (still used by `agent/submit.py`). It is superseded by the two-step form and is removed once `submit.py` switches.
- `parser.py`: verification-code extraction and `classify_status_email` -> `EmailStatusClassification` (prompt `email_status_classification`).
  - **What counts as a code:** a 4–12 character token that contains a digit (and is not a year). A code without digits is accepted only if it matches the board's declared `code_pattern`, is the whole text of a heading, appears once, and has mixed case, so that words and names are rejected. With a declared shape, every code must match it.
  - **Where:** within 80 characters of a code keyword (code, verification, verify, security, one-time, OTP, passcode), or alone in a heading of an email whose subject has a keyword.
  - **Which email:** `pick_verification_code(messages)` prefers a heuristic hit. Ties are broken by a keyword in the subject, then the closer candidate, then the newer email. So a confirmation email arriving together with the code email loses.
  - **LLM fallback:** only on emails that mention a code keyword (all emails when the board's subject filter is in the query). Its answer must appear in the email and pass the same shape rule.
  - Code values are never logged.
- `poller.py` `run_status_poller`: background task in the worker.
  - Every cycle, **including the first one at startup**, runs only while `gmail_status_polling_enabled` is `true`. Disabled, it makes no Gmail calls.
  - A cycle reads Gmail history, then fully fetches and LLM-classifies every new message with a body. It attributes the message to a run by the `To` `+alias`, otherwise by company name (`_match_by_company`, a loose `ILIKE`; tightening it is roadmap B7). It stores `application_status_update` and optionally auto-archives per status (`gmail_auto_archive`). Checkpoint key `status_poller`.
- `alert_poller.py` `run_alert_poller`: detects job-alert emails by sender patterns (`alert_parser.py`, algorithmic only), extracts URLs, fetches pages, scores with `compute_match_score` and inserts into `scheduled_job`. It receives the Temporal client to promote jobs. Checkpoint key `alert_poller`.
  - **Off switch:** every cycle (ingest, then promote), including the first one at startup, runs only while `job_alert_polling_enabled` is `true`. Disabled, it makes no Gmail calls, label changes or promotions. Promotion therefore also runs only while alert polling is enabled; `POST /scheduled/{id}/promote` is the manual path.
  - **Admission:** a job becomes `SCHEDULED` only if it was scored and the score is at least `job_alert_min_match_score` (0 = any score). If it could not be scored (nothing to score, or the scorer raised or returned no score), it is stored `SKIPPED` with `match_rationale = "not scored: <reason>"`. It is re-evaluated if it appears in a later alert.
  - **Stale alerts:** an alert older than `job_alert_max_age_days` (by Gmail `internalDate`) is left alone: no page fetches, no rows, no labels. This also bounds backfills.
  - **Promotion** (`plan_promotions`): `SCHEDULED` rows that are not on hold and are older than `job_alert_max_age_days` become `EXPIRED`, through a conditional `UPDATE`. Of the rest, rows whose `promote_after` has passed are promoted oldest first, at most `job_alert_max_promotions_per_cycle` per cycle (default 5; 0 = none). `APPROVED` rows are not auto-promoted (roadmap B3).
- **History sync** (both pollers): load the `gmail_checkpoint` row by key.
  - If there is none, the first run backfills via Gmail search (status: since the oldest run; alerts: since `job_alert_backfill_since`, otherwise it just seeds) and stores `get_current_history_id()`.
  - Otherwise it calls `GmailClient.get_history_since(history_id)` (history.list, `messageAdded`), and on `InvalidHistoryIdError` (checkpoint expired) falls back to search.
  - The checkpoint is advanced to the returned history id after each cycle, even past messages that failed. Fixing that, with a durable inbox, is roadmap B7.
- `get_history_since` follows `nextPageToken` across all pages. The search fallbacks are single-page (B7).
- Toggles are runtime settings ([data-model](data-model.md#configuration)). The alert keys default from `config.py` when absent from `settings.yaml`.
- `scripts/manual/gmail_verification.py` exercises live code retrieval. Its offline heuristic table predates the current rules.

## URL intake and identity (`src/yahbah/url_utils.py`)

- **`validate_job_url(url, *, resolver=None)`** raises `UnsafeURLError` (a `ValueError`) unless the URL is http(s), has no credentials, and its host is public. A public host is a public IP literal, or a name that resolves only to public addresses.
  - Rejected hosts: `localhost`/`*.localhost`, non-canonical numeric hosts (`2130706433`, `127.1`), and loopback, RFC 1918/ULA, link-local, CGNAT, multicast, reserved and unspecified addresses, also when embedded in IPv6 (mapped, 6to4, NAT64). A host that doesn't resolve is rejected too.
  - Tests inject `resolver`.
- **`fetch_public(method, url, *, resolver, transport, max_bytes=2 MiB)`** validates the URL and every redirect hop, as httpx parsed it, before requesting it. It follows at most 10 redirects by hand and reads at most `max_bytes` of the body.
- **`normalize_job_url`** (HEAD) and **`alert_parser.fetch_job_description`** (GET) both go through `fetch_public`.
  - `normalize_job_url` raises `UnsafeURLError`, and falls back to the original URL on other errors.
  - `fetch_job_description` returns `None` for a non-2xx or non-text response.
- **Where the policy applies:**
  - `POST /jobs` and `POST /scheduled` answer **422**;
  - alert link extraction skips the link;
  - `submit_job_for_application` re-checks it, so a stored URL cannot reach a workflow unchecked (`POST /scheduled/{id}/promote` → 422).
  - The browser agent does not apply the policy: it navigates on its own, and test fixtures are served from 127.0.0.1.
- **`canonicalize_url(url)`** (sync, idempotent) is `ats_normalize(strip_tracking(_normalize_url(url)))`.
  - `strip_tracking` removes a global list of trackers plus host-scoped ones (jobright.ai: `imp_id`). It keeps every other parameter exactly as written and in order, blank ones included.
  - `gh_jid` is kept, because it is the Greenhouse job id on company-hosted career pages (`boards.GREENHOUSE` matches on it).
- **Residual risk:** DNS can change between the check and the connection (DNS rebinding). Pinning the validated address at connect time would need a custom httpcore network backend.
