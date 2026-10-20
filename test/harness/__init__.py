"""Shared offline test harness for YahBah (owned by the QA task; see docs/wiki/development.md#tests).

Modules:
  env        process-wide guards installed by test/conftest.py before yahbah is imported:
             no app database, no network beyond localhost, no Ollama outside live_llm,
             artifacts and personal data redirected to a temp dir
  profile    the synthetic applicant (known answers, profile) every test uses
  server     local HTTP fixture server (stdlib http.server) with an event log
  browser    headless browser-use sessions that can only reach localhost
  llm        ScriptedLLM: OllamaClient replies scripted per role/schema, every call recorded
  agent      AgentScript: a scripted browser-use chat model that drives the real agent loops
  forms      submit-boundary helpers: counting claim, filled FillContext, page counters
  db         disposable Postgres database (yahbah_test_<hex>), migrated with alembic
  temporal   Temporal dev server, stub activities under their real names, the real worker
  api        the real FastAPI routers on a test app, with a fake or real Temporal client
  contract   shape-tolerant builders (dataclass outputs, model replies) and contract lookups
  fixtures   the pytest fixtures (re-exported by test/conftest.py)

The package is registered as `harness` by test/conftest.py; import it as `from harness import ...`.
"""
