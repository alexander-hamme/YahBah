# process-compose control port. Its default 8080 is already taken by an SSH tunnel on this machine.
export PC_PORT_NUM := "8085"

# Pass recipe arguments to commands as "$@" so quoted args survive (e.g. -m "not browser")
set positional-arguments

# List recipes
default:
    @just --list

# Install Python and frontend deps, create the Temporal state dir, apply migrations
setup:
    uv sync
    cd frontend && npm install
    mkdir -p ~/.temporal
    uv run alembic upgrade head

# Run Temporal, API, worker and dashboard in one TUI (Postgres and Ollama must already be running)
dev:
    mkdir -p ~/.temporal
    process-compose up

# Stop a running `just dev` from another terminal
down:
    process-compose down

# Restart one dev process (temporal | api | worker | dashboard)
restart proc:
    process-compose process restart {{proc}}

# Apply database migrations
migrate:
    uv run alembic upgrade head

# Offline pytest suite; extra args pass through (e.g. `just test -m "not browser"`)
test *args:
    uv run pytest "$@"

# Full suite including the e2e/C7 whole-run gate
test-all *args:
    uv run pytest --complete "$@"

# C7 acceptance gate (needs the Temporal dev server and Chrome)
c7:
    uv run pytest --complete -n 0 -q test/invariants/c7_acceptance

# Frontend tests, type check and lint
web-check:
    cd frontend && npm test && npx tsc --noEmit && npm run lint

# Everything offline: pytest plus frontend checks
check: test web-check

# Debug one job URL without Temporal (add --submit to actually submit)
pipeline url *flags:
    uv run python scripts/run_pipeline.py "$@"

# Seed the applicant profile from config/personal.yaml
seed-profile:
    uv run python scripts/seed_profile.py

# One-time Gmail OAuth flow
gmail-auth:
    uv run python -m yahbah.gmail.client

# Live answer eval: set = main | heldout. Plug the laptop in; run one eval at a time.
eval-answers set out:
    YAHBAH_EVAL_SET={{set}} YAHBAH_EVAL_OUT={{out}} caffeinate -i uv run pytest -m live_llm test/yahbah/llm/test_answer_eval.py -s -n 0

# Live verifier eval
eval-verify out:
    YAHBAH_EVAL_OUT={{out}} caffeinate -i uv run pytest -m live_llm test/yahbah/llm/test_verify_eval.py -s -n 0

# Live job-offers extraction eval
eval-offers out:
    YAHBAH_EVAL_OUT={{out}} caffeinate -i uv run pytest -m live_llm test/yahbah/llm/test_job_offers_eval.py -s -n 0
