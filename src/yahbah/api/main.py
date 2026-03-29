from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger
from temporalio.client import Client as TemporalClient

from yahbah.config import settings
from yahbah.api.routes import jobs, runs


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

app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(runs.router, prefix="/runs", tags=["runs"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
