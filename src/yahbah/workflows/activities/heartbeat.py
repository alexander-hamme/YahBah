"""
Background heartbeats for long activities (contract §7).

A long activity (browser stages, answer mapping) has a heartbeat_timeout of a few minutes, so a
dead worker is noticed in minutes and a timed-out attempt is cancelled locally before its retry
starts. Stage-level heartbeats (agent.discover.beat) can't cover a single 900 s model call, so
@heartbeating keeps beating from a background task for as long as the activity runs. Temporal
delivers cancellation through heartbeats too.
"""
import asyncio
import functools

from temporalio import activity

HEARTBEAT_EVERY = 20.0  # seconds; the SDK throttles what it actually sends


async def _beat_forever() -> None:
    while True:
        activity.heartbeat()
        await asyncio.sleep(HEARTBEAT_EVERY)


def heartbeating(fn):
    """Decorate an async activity (below @activity.defn) to heartbeat while it runs."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        beater = asyncio.create_task(_beat_forever())
        try:
            return await fn(*args, **kwargs)
        finally:
            beater.cancel()
    return wrapper
