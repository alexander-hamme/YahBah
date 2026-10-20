# Agent / Browser Automation (`src/yahbah/agent/`)

Replaces the deleted `browser/` package. The module docstring in `agent/__init__.py` has the full tier table; read it first. Uses `browser-use==0.13.11` (pinned in `pyproject.toml`) over local Ollama.

## Stages and tiers
| Stage | File | Tiers (code -> one-shot model -> agent) |
|---|---|---|
| discover (reach form) | `discover.py` | `read_form_schema` / click "Apply" -> element-pick call -> navigation agent |
| map_form | `discover.py` | `read_form_schema` -> cleaned-HTML-to-fields call (selectors verified on page) -> mapping agent |
| job info | `discover.py` | schema.org JSON-LD -> metadata model on page text |
| fill | `fill.py` | per-type executors (text, select, combobox, radio/checkbox, file via CDP), read-back diff + one retry -> fix agent (no submit) |
| submit/verify | `submit.py` | gate in code, click, verify loop (`settings.submit_max_attempts`), email-code and needs_fix handling, vision escalation |

Failures raise `StageFailed` (discover.py) with reasons such as `fill_failed`, `captcha`, `login_wall`, `job_closed`, `blocked`. `discover.map_form(force_tier="schema"|"oneshot"|"agent")` can start at a lower tier (used by `scripts/run_pipeline.py --force-tier`).

## Support files
- `tools.py`: our named browser-use tools (`read_form_schema`, dropdown readers, `look_at_screen`, gated `inspect_page_js` locked until a stall; deny-list is a guard rail, not a sandbox), `StallTracker`, `run_js`. Fields are addressed by an invisible `data-ja-field` key stamped on each element.
- `llm.py`: `chat_model(role)` (a `LoggingChatOllama` that logs raw replies), `Crew` (driver/vision/escalation), `Escalator` (hands steps to the escalation model on stall), `run_agent(task, crew, session, max_steps, output)` (agent tuned for slow local models). The step limit is passed by each caller and hard-coded there: navigation 6, dropdown reader 4, mapping agent 15 (`discover.py`), fill-fix agent 10 (`fill.py`). `settings.agent_max_steps` exists but is never read.
- `boards.py`: `BoardProfile` per job board (URL patterns, DOM signature, iframe embed patterns, stage hooks, `model_notes`, `submit_button_js`, and the verification-code email shape: `email_code_subject` as the Gmail subject filter and `email_code_pattern`, a regex the whole code must match, which Greenhouse sets to `[A-Za-z0-9]{8}`; `submit.py` passes both to the Gmail client, see [gmail](gmail.md)). Registered: `GREENHOUSE`, `GENERIC` fallback. `detect_board`, `embedded_form_url`, `with_notes`. Workday and Lever URLs are recognised elsewhere (Workday in `credentials.py` alias hashing and `gmail/alert_parser.py`; Lever in `alert_parser.py` and the company-domain heuristic in `workflows/activities/db_ops.py`) but have no `BoardProfile`, so they run as `generic`. The `resolve_application_url` hook (aggregator page -> real application URL) is called by `discover.py` when set, but no profile sets it. `record_observation` appends (board, which tier succeeded at each stage, field count) to `artifacts/_observations.jsonl`, but only from `map_form_activity` after `map_form` succeeds and from `scripts/run_pipeline.py` when its run completes without raising. Failed runs, and runs that never reach `map_form`, leave no observation. (The `boards.py` module docstring says "every run"; that is inaccurate.)
- `session.py`: `SessionRegistry` (singleton, `run_id` -> `BrowserSession`, own temp Chrome profile each) and `artifact_path(run_id, filename)`.

## Conventions
- Board-specific knowledge goes in a `BoardProfile`, not in generic code; fixtures guard against cross-board regressions ([development](../development.md)).
- Models only get roles from `yahbah/llm/models.py` ([llm](llm.md)); prompts are in `config/prompts.yaml` (`oneshot_*`, `agent_*_task`, `option_pick`, `post_submit_judge`).
- Activities wrapping these stages: `src/yahbah/workflows/activities/browser.py` ([workflows](workflows.md)).
