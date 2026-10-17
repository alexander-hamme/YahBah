# Gmail Integration (`src/yahbah/gmail/`)

All blocking Google API calls are wrapped in `asyncio.to_thread`. OAuth setup: credentials in `settings.gmail_credentials_dir` (default `~/.config/yahbah/gmail`); one-time consent via `uv run python -m yahbah.gmail.client`.

- `client.py` `GmailClient`: search, history list, labels, archive.
- `parser.py`: verification-code extraction (heuristic `<h1>` for Greenhouse, LLM fallback) and `classify_status_email` -> `EmailStatusClassification` (prompt `email_status_classification`). Used by `agent/submit.py` when the post-submit verdict is `email_code`.
- `poller.py` `run_status_poller`: background task in the worker. Reads Gmail history, keeps only `+alias` recipients, classifies, stores `application_status_update` linked to the run, optionally auto-archives per status (`gmail_auto_archive`). Always runs one poll on startup; then loops only if `gmail_status_polling_enabled`. Checkpoint key `status_poller`.
- `alert_poller.py` `run_alert_poller`: detects job-alert emails by sender patterns (`alert_parser.py`, algorithmic only), extracts URLs, fetches pages, scores with `compute_match_score`, inserts into `scheduled_job`, and promotes jobs after `job_alert_promotion_delay_hours` by starting workflows (it receives the Temporal client). Checkpoint key `alert_poller`.
- History sync pattern (both pollers): load `gmail_checkpoint` row by key; if none, first run backfills via Gmail search (status: since oldest submitted run; alerts: since `job_alert_backfill_since`, else just seed) and stores `get_current_history_id()`; otherwise `GmailClient.get_history_since(history_id)` (history.list, `messageAdded`). On `InvalidHistoryIdError` (checkpoint expired) fall back to search. The checkpoint is advanced to the returned history id after each cycle.
- `get_history_since` follows `nextPageToken` across all pages, so a backlog is never dropped while the checkpoint advances.
- Toggles are runtime settings ([data-model](data-model.md#configuration)).
- `scripts/manual/gmail_verification.py` exercises live code retrieval.
