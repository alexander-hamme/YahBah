# Development

## Run (three processes + deps)
`temporal server start-dev`; `uv run uvicorn yahbah.api.main:app --reload --port 8000`; `uv run python -m yahbah.worker`. Needs Postgres, Ollama with the configured models pulled, and a Chrome/Chromium for browser-use (it looks for system Chrome or Playwright's Chromium cache, `~/Library/Caches/ms-playwright` or `PLAYWRIGHT_BROWSERS_PATH`, and otherwise runs `uvx playwright install chromium` itself; pre-install with that command if you like). Full setup is in the root `README.md`. Setup: `uv sync`, `uv run alembic upgrade head` (as DB owner, see [data-model](modules/data-model.md)), `uv run python scripts/seed_profile.py`.

## Tests (`test/`, pytest, asyncio_mode=auto; config in `pyproject.toml`)
`uv run pytest`. Tests: `test/yahbah/agent/` (`test_fixtures.py`, `test_fill_logic.py`, `test_stall_escalation.py`), `test/yahbah/llm/test_field_mapper.py`. Marker `browser` = launches headless Chrome on offline fixture pages (`test/yahbah/fixtures/`, cases in `cases.json`), no network. Skip with `-m "not browser"`. Add a fixture whenever a page is verified by hand.

## Scripts (`scripts/`)
- `run_pipeline.py <url>`: run discover -> map -> answers -> fill without Temporal/run record; never submits without `--submit`; flags `--force-tier`, `--cover-letter`, `--headed`, `--keep-open`; output `artifacts/debug-<ts>/`.
- `save_fixture.py <url> <name>`: save a live page as an offline fixture.
- `seed_profile.py`: seed applicant profile from the `profile` key of gitignored `config/personal.yaml` (merged by `load_prompts_config`; template: `config/personal.example.yaml`).
- `manual/gmail_verification.py <email>`: hand-run live check of verification-code retrieval, not collected by pytest.
- `imgs_to_gif.py`. (The Phase 0 browser-use spike script was removed; see commit `d1965aa`.)

## Docs
- Root `README.md` is the human-facing entry point (setup, run, tests); this wiki holds the depth.
- Debugging an agent run: `artifacts/<run_id>/llm/raw.jsonl` and `steps/` ([llm](modules/llm.md)).
