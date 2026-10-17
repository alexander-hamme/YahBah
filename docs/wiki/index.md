# YahBah Wiki Index

Read this first, then open only the pages you need. Source is the authority; pages note where they may lag.
State: branch `browser-use`, mid "agentic revamp" (uncommitted work in tree). See [architecture](architecture.md) for what is in flux.

| Page | Covers | Read when |
|---|---|---|
| [architecture.md](architecture.md) | Processes, end-to-end flow, design rules, what is in flux | Starting any task; need the big picture |
| [modules/agent.md](modules/agent.md) | `src/yahbah/agent/`: tiered code/one-shot/agent browser automation, board profiles, submit gating | Touching discover/map/fill/submit, a job board, or browser tools |
| [modules/workflows.md](modules/workflows.md) | Temporal workflow, activities, worker, run slots, session registry | Changing run stages, retries, timeouts, activity inputs (`schemas.py`) |
| [modules/llm.md](modules/llm.md) | Ollama model roles, `OllamaClient`, prompts (`config/prompts.yaml`), field mapper, answer cache, LLM logs | Changing models, prompts, answer mapping, cover letters |
| [modules/data-model.md](modules/data-model.md) | DB tables, alembic migrations, config/settings files | Schema changes, migrations, config keys |
| [modules/gmail.md](modules/gmail.md) | Gmail client, verification codes, status poller, job-alert poller | Email-related behaviour |
| [modules/api-frontend.md](modules/api-frontend.md) | FastAPI routes and the Next.js frontend | Adding endpoints or UI |
| [development.md](development.md) | Setup/run commands, tests, scripts, stale docs | Running, testing, debugging a board |

Maintenance rules live in the root `CLAUDE.md`.
