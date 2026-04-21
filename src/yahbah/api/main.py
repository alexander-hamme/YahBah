from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from temporalio.client import Client as TemporalClient

from yahbah.config import settings, get_runtime_settings, set_runtime_setting
from yahbah.api.routes import applications, jobs, runs, scheduled


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API starting up")
    app.state.temporal = await TemporalClient.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
    )
    yield
    logger.info("API shutting down")


app = FastAPI(
    title="YahBah — Autonomous Job Application API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(applications.router, prefix="/applications", tags=["applications"])
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(runs.router, prefix="/runs", tags=["runs"])
app.include_router(scheduled.router, prefix="/scheduled", tags=["scheduled"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/settings")
async def get_settings() -> dict:
    return await get_runtime_settings()


from pydantic import BaseModel as _BaseModel
from typing import Any as _Any

class _SettingUpdate(_BaseModel):
    value: _Any

@app.put("/settings/{key}")
async def update_setting(key: str, body: _SettingUpdate) -> dict:
    return await set_runtime_setting(key, body.value)


@app.post("/gmail/poll")
async def trigger_gmail_poll() -> dict:
    """Manually trigger a Gmail status poll cycle."""
    from yahbah.gmail.poller import trigger_poll
    try:
        processed, skipped = await trigger_poll()
        return {"success": True, "processed": processed, "skipped": skipped}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.post("/gmail/alert-poll")
async def trigger_gmail_alert_poll() -> dict:
    """Manually trigger a Gmail job alert poll cycle."""
    from yahbah.gmail.alert_poller import trigger_alert_poll
    try:
        processed, skipped = await trigger_alert_poll()
        return {"success": True, "processed": processed, "skipped": skipped}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


class _BackfillRequest(_BaseModel):
    since: str  # ISO date, e.g. "2026-04-10"

@app.post("/gmail/alert-backfill")
async def trigger_alert_backfill(body: _BackfillRequest) -> dict:
    """Delete the alert checkpoint and backfill from a given date."""
    from yahbah.gmail.alert_poller import trigger_backfill
    try:
        processed, skipped = await trigger_backfill(body.since)
        return {"success": True, "processed": processed, "skipped": skipped}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
